"""后台精修 worker：导入先展示本地结果后，用 AI 并发批量清洗更新。

在独立子进程（pdf_worker_cli mode=refine）中执行（fitz 限制），
完成后替换该模组的全部分段并写入自动抽取的词库。
"""
import json
import os
import subprocess
import sys as _sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class RefineWorker(QThread):
    """AI 精修 worker。"""

    progress = Signal(int, str)
    finished = Signal(str, int)   # file_name, 分段数
    failed = Signal(str)

    def __init__(self, path: str, db, parent=None):
        super().__init__(parent)
        self._path = path
        self._db = db

    def run(self) -> None:
        try:
            from src.modules.llm_client import build_api_config

            config = {"path": self._path, "mode": "refine",
                      "api": build_api_config()}
            env = dict(os.environ)
            env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                [_sys.executable, "-m", "src.modules.pdf_worker_cli"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                cwd=str(PROJECT_ROOT), env=env,
            )
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(json.dumps(config))
            proc.stdin.close()

            result_data = None
            error = None
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line.startswith("PROGRESS|"):
                    _, pct, status = line.split("|", 2)
                    try:
                        self.progress.emit(int(pct), status)
                    except ValueError:
                        pass
                elif line.startswith("RESULT|"):
                    try:
                        result_data = json.loads(line[len("RESULT|"):])
                    except json.JSONDecodeError as exc:
                        error = f"精修结果解析失败：{exc}"
                elif line.startswith("ERROR|"):
                    error = line[len("ERROR|"):]
            proc.wait(timeout=3600)
            if error:
                self.failed.emit(error)
                return
            if proc.returncode != 0 or result_data is None:
                stderr_tail = (proc.stderr.read() if proc.stderr else "")[-2000:]
                self.failed.emit(
                    f"精修子进程异常（退出码 {proc.returncode}）\n{stderr_tail}")
                return

            filename = result_data["filename"]
            segments = result_data.get("segments") or []
            keywords = result_data.get("keywords") or []
            self._db.clear_file(filename)
            self._db.insert_segments(segments, filename)
            added = sum(
                1 for kw in keywords
                if self._db.add_keyword(kw.get("name", ""), kw.get("kind", ""),
                                        kw.get("detail", ""), filename))
            if added:
                self.progress.emit(97, f"词库新增 {added} 条")
            self.finished.emit(filename, len(segments))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
