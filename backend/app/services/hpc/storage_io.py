"""Atomic replacement with bounded Windows sharing/AV contention retries."""
import os
from time import sleep


def atomic_replace(source, destination):
    for attempt in range(10):
        try:
            os.replace(source,destination)
            return
        except PermissionError:
            if attempt == 9:
                raise
            sleep(min(.25,.02 * 1.5**attempt))
