"""Process startup utilities; importing adapters never creates runtime files."""
import fcntl
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def prune_logs(prefix):
    directory = ROOT / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    # Only diagnostic logs; raw captures and evaluator evidence are retained.
    logs = sorted(directory.glob(f"{prefix}_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in logs[50:]:
        if time.time() - path.stat().st_mtime > 7 * 86400:
            path.unlink(missing_ok=True)
    return directory


def configure_logging(prefix):
    directory = prune_logs(prefix)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(
                            directory / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.log",
                            encoding="utf-8")])


class IngestionLock:
    """Kernel-owned advisory lease released even after a process crash."""
    def __init__(self, db_path, series):
        path = Path(db_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = (path.parent / f".{path.name}.{series}.ingestion.lock").open("a+")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise RuntimeError(f"a {series} writer already owns this database")
        self.file.seek(0)
        self.file.truncate()
        self.file.write(str(os.getpid()))
        self.file.flush()

    def close(self):
        if not self.file.closed:
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()
