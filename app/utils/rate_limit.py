"""Sliding-window counter for login throttling."""

import threading
import time
from collections import defaultdict, deque


class SlidingWindow:
    # ponytail: in-process memory, so each uvicorn worker counts separately and
    # counts reset on restart. Move to Redis/DB if the limit must be exact.
    def __init__(self, limit: int, seconds: int):
        self.limit = limit
        self.seconds = seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        hits = self._hits[key]
        while hits and hits[0] <= now - self.seconds:
            hits.popleft()
        if not hits:
            del self._hits[key]
            hits = self._hits[key]
        return hits

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.limit

    def hit(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


if __name__ == "__main__":
    w = SlidingWindow(limit=2, seconds=60)
    assert not w.blocked("a")
    w.hit("a"); w.hit("a")
    assert w.blocked("a") and not w.blocked("b")
    w.reset("a")
    assert not w.blocked("a")
    w2 = SlidingWindow(limit=1, seconds=0)
    w2.hit("x")
    assert not w2.blocked("x")  # window of 0s expires immediately
    print("ok")
