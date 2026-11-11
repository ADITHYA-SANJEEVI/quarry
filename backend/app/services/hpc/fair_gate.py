"""FIFO feature-batch admission; timed-out tickets cannot block successors."""
from collections import deque
from threading import Condition
from time import monotonic


class FairGate:
    def __init__(self):
        self._condition = Condition()
        self._waiting = deque()
        self._busy = False

    def acquire(self, timeout=None):
        ticket = object()
        deadline = None if timeout is None else monotonic() + timeout
        with self._condition:
            self._waiting.append(ticket)
            while self._busy or self._waiting[0] is not ticket:
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    self._waiting.remove(ticket)
                    self._condition.notify_all()
                    return False
                self._condition.wait(remaining)
            self._waiting.popleft()
            self._busy = True
            return True

    def release(self):
        with self._condition:
            self._busy = False
            self._condition.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()
