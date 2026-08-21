"""任务队列与批量编排：asyncio.Queue 按章节顺序处理，支持并发/暂停/断点续传。

- 队列按章节顺序入队，最多 concurrency 个任务同时执行（默认 2，可在设置页调整）。
- 暂停/继续通过 asyncio.Event 控制；取消通过 cancel 事件。
- 断点续传：每完成一个章节把状态写入 SQLite（task_progress 表），
  重启后跳过已完成的章节，从上次中断处继续。
"""
import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from src.modules.database import Database


@dataclass
class Task:
    """单个章节任务。"""

    chapter_index: int
    chapter: str
    file_name: str
    payload: dict = field(default_factory=dict)


class TaskCancelled(Exception):
    """任务被用户取消。"""


class TaskQueueRunner:
    """基于 asyncio 的章节任务编排器。

    用法：
        runner = TaskQueueRunner(tasks, process_fn, db, concurrency=2, task_type="translate")
        results = await runner.run(progress_cb=...)

    process_fn 签名：async def process_fn(task: Task) -> object
    progress_cb 签名：progress_cb(done: int, total: int, status: str)
    """

    def __init__(
        self,
        tasks: list[Task],
        process_fn: Callable[[Task], Awaitable[object]],
        db: Database,
        concurrency: int = 2,
        task_type: str = "task",
    ):
        self._tasks = list(tasks)
        self._process_fn = process_fn
        self._db = db
        self._concurrency = max(1, int(concurrency))
        self._task_type = task_type
        self._queue: asyncio.Queue = asyncio.Queue()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._cancel_event = asyncio.Event()
        self._results: dict[int, object] = {}
        self._errors: dict[int, str] = {}
        self._done_count = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- 控制（在事件循环线程外调用） ----
    def _run_threadsafe(self, fn) -> None:
        """把事件循环相关操作切回循环线程执行（线程安全）。"""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(fn)
        else:
            fn()

    def pause(self) -> None:
        """暂停处理：当前任务完成后不再取新任务。"""
        self._run_threadsafe(self._pause_event.clear)

    def resume(self) -> None:
        """继续处理。"""
        self._run_threadsafe(self._pause_event.set)

    def cancel(self) -> None:
        """取消剩余任务（正在执行的任务完成后停止）。"""
        self._run_threadsafe(self._cancel_event.set)
        self._run_threadsafe(self._pause_event.set)

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    # ---- 运行 ----
    async def run(
        self,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> dict:
        """按章节顺序执行队列；返回 {chapter_index: 结果}。"""
        self._loop = asyncio.get_running_loop()
        # 断点续传：跳过已完成章节
        pending = [
            t for t in self._tasks
            if not self._db.is_task_done(self._task_type, t.file_name, t.chapter)
        ]
        skipped = len(self._tasks) - len(pending)
        self._done_count = 0
        total = len(pending)
        if progress_cb:
            progress_cb(0, total, f"待处理 {total} 章（已跳过 {skipped} 章）")

        for task in pending:
            self._queue.put_nowait(task)

        workers = [
            asyncio.create_task(self._worker(progress_cb, total))
            for _ in range(self._concurrency)
        ]
        await self._queue.join()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

        if progress_cb:
            progress_cb(self._done_count, total, "全部完成")
        if self._errors:
            first = next(iter(self._errors.values()))
            raise RuntimeError(
                f"{len(self._errors)} 个章节失败，例如：{first[:200]}")
        return self._results

    async def _worker(
        self,
        progress_cb: Callable[[int, int, str], None] | None,
        total: int,
    ) -> None:
        """并发 worker：取任务、处理、写断点。"""
        while not self._cancel_event.is_set():
            try:
                task = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                # 暂停点：两任务之间生效
                if not self._pause_event.is_set():
                    await self._pause_event.wait()
                if self._cancel_event.is_set():
                    self._queue.task_done()
                    return
                result = await self._process_fn(task)
                self._results[task.chapter_index] = result
                self._db.mark_task_done(
                    self._task_type, task.file_name, task.chapter)
            except TaskCancelled:
                pass
            except Exception as exc:  # noqa: BLE001  单章失败不阻塞其余章节
                self._errors[task.chapter_index] = str(exc)
            finally:
                self._queue.task_done()
                self._done_count += 1
                if progress_cb:
                    progress_cb(self._done_count, total, f"完成：{task.chapter}")
        # 取消时清空队列
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
