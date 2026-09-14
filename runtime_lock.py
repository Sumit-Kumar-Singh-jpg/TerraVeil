"""Prevent duplicate Mother Host processes from competing for the serial port."""
import os
from pathlib import Path


class InstanceLock:
    def __init__(self, path):
        self.file = None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open('a+b')
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError('TerraVeil is already running. Open http://localhost:5000; do not start a second copy.') from None
        self.file = handle

    def close(self):
        if self.file:
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None
