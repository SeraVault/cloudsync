"""Persistent user-visible activity log for sync events and errors."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .config import DATA_DIR

LOG_FILE = DATA_DIR / "activity.log"
_MAX_ENTRIES = 500


@dataclass
class ActivityEntry:
    timestamp: str
    level: str
    message: str
    provider: str = ""


class ActivityLog:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or LOG_FILE

    def append(
        self,
        level: str,
        message: str,
        provider: str = "",
    ) -> ActivityEntry:
        entry = ActivityEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=level.lower(),
            message=message,
            provider=provider,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as handle:
            json.dump(entry.__dict__, handle)
            handle.write("\n")
        self._rotate()
        return entry

    def _rotate(self) -> None:
        """Trim the log file to at most _MAX_ENTRIES lines (atomic rewrite)."""
        try:
            with open(self._path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return
        if len(lines) <= _MAX_ENTRIES:
            return
        trimmed = lines[-_MAX_ENTRIES:]
        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent, prefix=".activity-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(trimmed)
            os.replace(tmp, self._path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def recent(self, limit: int = 30) -> List[ActivityEntry]:
        if not self._path.exists():
            return []

        try:
            with open(self._path, encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return []

        entries: List[ActivityEntry] = []
        for line in reversed(lines[-limit:]):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.append(ActivityEntry(**payload))
        return entries

    def recent_text(self, limit: int = 30) -> str:
        entries = self.recent(limit)
        if not entries:
            return "No activity recorded yet."

        lines = []
        for entry in entries:
            provider = f" [{entry.provider}]" if entry.provider else ""
            lines.append(
                f"{entry.timestamp} {entry.level.upper()}{provider} {entry.message}"
            )
        return "\n".join(lines)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)