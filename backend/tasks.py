"""Fire-and-forget asyncio task tracking."""

import asyncio

from backend.logger import logger

background_tasks: set[asyncio.Task] = set()


def _log_background_task_result(task: asyncio.Task) -> None:
    background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task %s failed", task.get_name(), exc_info=exc)


def spawn_background_task(coro, name: str) -> asyncio.Task:
    # Keep a strong reference so the event loop cannot garbage-collect the
    # task mid-flight, and log any exception it dies with.
    task = asyncio.get_running_loop().create_task(coro, name=name)
    background_tasks.add(task)
    task.add_done_callback(_log_background_task_result)
    return task
