"""Shared backend logger."""

import logging
from typing import Any


LOGGER_NAME = "coloma"
logger = logging.getLogger(LOGGER_NAME)


class SuccessfulAccessLogFilter(logging.Filter):
    """Drop noisy successful Uvicorn access logs while keeping failures visible."""

    def filter(self, record: logging.LogRecord) -> bool:
        status_code = _access_log_status_code(record.args)
        if status_code is None:
            return True
        return not 200 <= status_code < 300


def _access_log_status_code(args: Any) -> int | None:
    if not isinstance(args, tuple) or not args:
        return None
    status_code = args[-1]
    return status_code if isinstance(status_code, int) else None


def configure_access_log_filter() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if any(isinstance(filter_, SuccessfulAccessLogFilter) for filter_ in access_logger.filters):
        return
    access_logger.addFilter(SuccessfulAccessLogFilter())


configure_access_log_filter()
