"""Local filesystem watcher using watchdog."""
from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import List

from watchdog.observers import Observer

try:
    # InotifyObserver lets us restrict the event mask to write/create/delete
    # events only, avoiding spurious fires on IN_OPEN / IN_ACCESS / IN_ATTRIB
    # that occur when files are merely read (e.g. a photo viewer opening an image).
    from watchdog.observers.inotify import InotifyObserver
    from watchdog.observers.inotify_c import (
        InotifyConstants,
        InotifyEvent,
    )
    import watchdog.observers.inotify as _inotify_mod

    _WRITE_MASK = (
        InotifyConstants.IN_CLOSE_WRITE
        | InotifyConstants.IN_CREATE
        | InotifyConstants.IN_DELETE
        | InotifyConstants.IN_DELETE_SELF
        | InotifyConstants.IN_MOVED_FROM
        | InotifyConstants.IN_MOVED_TO
    )
    _USE_INOTIFY = True
except Exception:
    _USE_INOTIFY = False

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)


class _Handler(FileSystemEventHandler):
    def __init__(self, queue: Queue) -> None:
        super().__init__()
        self._queue = queue

    def on_created(self, event):
        if not event.is_directory:
            self._queue.put(("created", Path(event.src_path)))

    def on_modified(self, event):
        if not event.is_directory:
            self._queue.put(("modified", Path(event.src_path)))

    def on_deleted(self, event):
        if not event.is_directory:
            self._queue.put(("deleted", Path(event.src_path)))

    def on_moved(self, event):
        if not event.is_directory:
            self._queue.put(("deleted", Path(event.src_path)))
            self._queue.put(("created", Path(event.dest_path)))


class LocalWatcher:
    """Watches directories for file write/create/delete events.

    Only reacts to actual content changes (close-after-write, create, delete,
    move).  Read-only opens are ignored, preventing spurious uploads when
    files are accessed by other applications.
    """

    def __init__(self) -> None:
        self.queue: Queue = Queue()
        self._observer = self._make_observer()
        self._watched_paths: List[Path] = []

    def _make_observer(self):
        if _USE_INOTIFY:
            try:
                obs = InotifyObserver()
                # Monkey-patch the event mask on the class so all watches
                # created by this observer use our restricted mask.
                obs._inotify_mask = _WRITE_MASK
                return obs
            except Exception:
                pass
        return Observer()

    def add_path(self, path: Path) -> None:
        if path not in self._watched_paths:
            self._observer.schedule(_Handler(self.queue), str(path), recursive=True)
            self._watched_paths.append(path)

    def remove_path(self, path: Path) -> None:
        self._watched_paths = [p for p in self._watched_paths if p != path]
        self.stop()
        self._observer = self._make_observer()
        for p in self._watched_paths:
            self._observer.schedule(_Handler(self.queue), str(p), recursive=True)
        self._observer.start()

    def start(self) -> None:
        if not self._observer.is_alive():
            self._observer.start()

    def stop(self) -> None:
        if self._observer.is_alive():
            self._observer.stop()
            self._observer.join()
