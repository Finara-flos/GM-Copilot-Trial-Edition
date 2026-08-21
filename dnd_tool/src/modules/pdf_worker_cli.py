"""PDF 导入子进程入口：在独立进程中执行 fitz 解析与 LLM 清洗。

主程序进程不应加载 PyMuPDF（fitz 会污染同进程其他线程的网络），
因此 PDF 导入统一由本子进程完成，结果经 stdout 传回主程序。

协议（stdout，每行一条）：
    PROGRESS|<percent>|<status>        进度
    RESULT|<json>                       最终结果：{"filename","segments","keywords"}
非 PDF 文件由主程序进程内解析，不走本子进程。

用法：python -m src.modules.pdf_worker_cli < stdin.json
    stdin.json: {"path": "...", "mode": "fast"|"refine",
                 "api": {"base_url","api_key","model","protocol"} | null}

- mode=fast：本地启发式解析（页眉过滤/章节识别/怪物分离），立即入库显示。
- mode=refine：异步并发批量 LLM 清洗（精修），耗时较长，用于后台更新。
"""
import asyncio
import json
import sys


def main() -> int:
    # 强制 UTF-8 输出，避免中文进度在管道中按 GBK 编码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    config = json.loads(sys.stdin.read() or "{}")
    path = config.get("path", "")
    if not path:
        print("ERROR|缺少 path", flush=True)
        return 2
    mode = config.get("mode", "fast")
    api = config.get("api") or None

    def progress(pct: int, status: str) -> None:
        print(f"PROGRESS|{int(pct)}|{status}", flush=True)

    if mode == "refine":
        from src.core.api_client import AsyncLLMClient
        from src.modules.importer import _parse_pdf_smart_async

        client = AsyncLLMClient(api)
        ok, reason = client.ready()
        if not ok:
            print(f"ERROR|{reason}", flush=True)
            return 3
        segments, filename, keywords = asyncio.run(
            _parse_pdf_smart_async(client, path, progress, True))
    else:
        from src.modules.importer import parse_document

        segments, filename, keywords = parse_document(
            path, progress, llm=None, extract_keywords=False)

    payload = json.dumps({
        "filename": filename,
        "segments": segments,
        "keywords": keywords,
    }, ensure_ascii=False)
    print(f"RESULT|{payload}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
