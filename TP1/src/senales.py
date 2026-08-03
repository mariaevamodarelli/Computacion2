import json
import os
import signal
import time


MONITORED_SIGNALS = [signal.SIGINT, signal.SIGTERM]

for signal_name in ("SIGHUP", "SIGUSR1", "SIGUSR2", "SIGWINCH"):
    if hasattr(signal, signal_name):
        MONITORED_SIGNALS.append(getattr(signal, signal_name))


class SignalController:
    """Implementa self-pipe con signal.set_wakeup_fd."""

    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        os.set_blocking(self.read_fd, False)
        os.set_blocking(self.write_fd, False)
        signal.set_wakeup_fd(self.write_fd)
        for sig in MONITORED_SIGNALS:
            signal.signal(sig, self._handler)

    def _handler(self, signum, _frame):
        # El trabajo real se hace fuera del handler. set_wakeup_fd despierta el loop.
        return None

    def read_pending(self):
        pending = []
        while True:
            try:
                data = os.read(self.read_fd, 1024)
            except BlockingIOError:
                break
            if not data:
                break
            pending.extend(data)
        return pending

    def close(self):
        signal.set_wakeup_fd(-1)
        os.close(self.read_fd)
        os.close(self.write_fd)


def dump_snapshot(snapshot, directory="."):
    plain = {key: dict(value) for key, value in snapshot.items()}
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(directory, f"dump_{timestamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plain, f, indent=2, ensure_ascii=False)
    return path


def load_config(path="config.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
