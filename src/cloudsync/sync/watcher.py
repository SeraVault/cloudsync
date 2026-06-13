"""Local filesystem watcher using watchdog."""
from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import List

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

try:
    from watchdog.observers.inotify import InotifyObserver
    _USE_INOTIFY = True
except Exception:
    _USE_INOTIFY = False


# Temp/swap file patterns that should never be synced.
# .goutputstream-* — GNOME atomic saves
# *~              — editor backups (gedit, emacs, etc.)
# .#*             — emacs lock files
# .~*             — LibreOffice temp files
def _is_temp(path: Path) -> bool:
    name = path.name
    return (
        name.startswith(".goutputstream-")
        or name.endswith("~")
        or name.startswith(".#")
        or name.startswith(".~")
    )


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: Queue) -> None:
        super().__init__()
        self._queue = queue

    def on_created(self, event):
        if not event.is_directory:
            path = Path(event.src_path)
            if not _is_temp(path):
                self._queue.put(("created", path))

    def on_modified(self, event):
        if not event.is_directory:
            path = Path(event.src_path)
            if not _is_temp(path):
                self._queue.put(("modified", path))

    def on_deleted(self, event):
        if not event.is_directory:
            self._queue.put(("deleted", Path(event.src_path)))

    def on_moved(self, event):
        if not event.is_directory:
            self._queue.put(("deleted", Path(event.src_path)))
            dest = Path(event.dest_path)
            if not _is_temp(dest):
                self._queue.put(("created", dest))


class LocalWatcher:
    """Watches directories for file write/create/delete events.

    Only reacts to actual content changes (close-after-write, create, delete,
    move).  Read-only opens are ignored, preventing spurious uploads when
    files are accessed by other applications.
    """

    def __init__(self) -> None:
        self.queue: Queue = Queue()
        self._observer = InotifyObserver() if _USE_INOTIFY else Observer()
        self._watched_paths: List[Path] = []

    def add_path(self, path: Path) -> None:
        if path not in self._watched_paths:
            self._observer.schedule(
                _Handler(self.queue),
                str(path),
                recursive=True,
            )
            self._watched_paths.append(path)

    def remove_path(self, path: Path) -> None:
        self._watched_paths = [p for p in self._watched_paths if p != path]
        self.stop()
        self._observer = InotifyObserver() if _USE_INOTIFY else Observer()
        for p in self._watched_paths:
            self._observer.schedule(
                _Handler(self.queue),
                str(p),
                recursive=True,
            )
        self._observer.start()

    def start(self) -> None:
        if not self._observer.is_alive():
            self._observer.start()

    def stop(self) -> None:
        if self._observer.is_alive():
            self._observer.stop()
            self._observer.join()
