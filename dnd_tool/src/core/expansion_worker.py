"""扩写流水线后台 worker：QThread 内运行 asyncio 事件循环。

- 遍历 need_expand 分段，经 TaskQueueRunner 并发处理（断点续传、暂停/继续）。
- 每段生成多个版本；"总是询问"模式下等待用户采纳后再继续。
- 流式输出通过 stream 信号实时上报（打字机效果）。
"""
import asyncio
import threading

from PySide6.QtCore import QThread, Signal

from src.core.task_queue import Task, TaskQueueRunner
from src.modules.segment_analyzer import analyze_segments


class ExpansionWorker(QThread):
    """全模组自动扩写 worker。"""

    progress = Signal(int, int, str)              # done, total, status
    stream = Signal(str)                          # 流式增量
    segment_done = Signal(int, list)              # segment_id, [版本...]
    awaiting_confirm = Signal(int, list)          # 总是询问：等待用户采纳
    finished_ok = Signal(str, int)                # file_name, 扩写段数
    failed = Signal(str)

    def __init__(self, file_name: str, db, ask_each: bool = False,
                 num_versions: int = 3, parent=None):
        super().__init__(parent)
        self._file = file_name
        self._db = db
        self._ask_each = ask_each
        self._num_versions = max(2, int(num_versions))
        self._runner: TaskQueueRunner | None = None
        self._confirm_event = threading.Event()
        self._confirm_version = 0
        self._cancelled = False

    # ---- 控制 ----
    def pause(self) -> None:
        if self._runner is not None:
            self._runner.pause()

    def resume(self) -> None:
        if self._runner is not None:
            self._runner.resume()

    def is_paused(self) -> bool:
        return bool(self._runner and self._runner.is_paused())

    def confirm_version(self, version_index: int) -> None:
        """用户采纳某个版本（总是询问模式）。"""
        self._confirm_version = version_index
        self._confirm_event.set()

    def cancel_ask(self) -> None:
        """总是询问模式下取消等待（按默认版本处理）。"""
        self._confirm_event.set()

    # ---- 主流程 ----
    def run(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    async def _run(self) -> None:
        from src.core.api_client import AsyncLLMClient
        from src.core.expansion_pipeline import expand_segment

        client = AsyncLLMClient()
        ok, reason = client.ready()
        if not ok:
            self.failed.emit(reason)
            return

        segments = analyze_segments(self._db.get_segments(self._file), self._db)
        pending = [
            s for s in segments
            if s.get("decision") == "need_expand" and not self._db.has_expanded(s["id"])
        ]
        skipped = len(segments) - len(pending)
        total = len(pending)
        if total == 0:
            self.progress.emit(0, 0, f"无需扩写的段落（{skipped} 段已处理）")
            self.finished_ok.emit(self._file, 0)
            return
        self.progress.emit(0, total, f"待扩写 {total} 段（已跳过 {skipped} 段）")

        tasks = [
            Task(index, str(seg["id"]), self._file, {"seg": seg})
            for index, seg in enumerate(pending)
        ]

        async def process_fn(task: Task):
            seg = task.payload["seg"]
            stream = lambda s: self.stream.emit(s)  # noqa: E731
            versions = await expand_segment(
                client, seg["content"], self._num_versions, stream_cb=stream)
            if self._ask_each:
                self._confirm_event.clear()
                self.awaiting_confirm.emit(seg["id"], versions)
                while not self._confirm_event.is_set():
                    if self._cancelled:
                        break
                    await asyncio.sleep(0.1)
                select = self._confirm_version if not self._cancelled else 0
            else:
                select = 0
            self._db.save_expanded_versions(seg["id"], versions, select)
            self.segment_done.emit(seg["id"], versions)
            return versions

        runner = TaskQueueRunner(
            tasks, process_fn, self._db,
            concurrency=2, task_type="expansion")
        self._runner = runner
        self._partial_error = ""
        results: dict = {}
        try:
            results = await runner.run(
                progress_cb=lambda d, t, s: self.progress.emit(d, t, s))
        except RuntimeError as exc:
            # 部分段落失败：仍导出已完成的内容
            self._partial_error = str(exc)
            results = runner._results
        self.finished_ok.emit(self._file, len(results))
