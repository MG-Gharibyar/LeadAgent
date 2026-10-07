from __future__ import annotations

import json
import logging

from .models import utcnow


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"at": utcnow(), "level": record.levelname, "event": record.getMessage()}
        for name in ("run_id", "error_type"):
            if hasattr(record, name):
                data[name] = getattr(record, name)
        return json.dumps(data)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.getLogger().handlers = [handler]
    logging.getLogger().setLevel(logging.INFO)
