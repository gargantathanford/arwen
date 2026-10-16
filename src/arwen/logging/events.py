"""Explicitly owned JSONL event files; no logging configuration on import."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO


class JsonEvents:
    def __init__(self, path: Path, clock: Callable[[], float]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = path.open("x", encoding="utf-8")
        self._clock = clock

    def write(self, event: str, fields: dict[str, object]) -> None:
        record = {
            "utc": datetime.now(UTC).isoformat(),
            "monotonic_s": self._clock(),
            "event": event,
            **fields,
        }
        self._file.write(json.dumps(record, allow_nan=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
