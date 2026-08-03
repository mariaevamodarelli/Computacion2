import queue
import time

from src.procfs import list_pids


def run(task_queues, stop_event, heartbeat_conn=None):
    """Lista /proc y distribuye la lista de PIDs a todos los analizadores."""
    while not stop_event.is_set():
        pids = list_pids()
        for task_queue in task_queues.values():
            try:
                if task_queue.qsize() < 2:
                    task_queue.put_nowait(pids)
            except NotImplementedError:
                try:
                    task_queue.put_nowait(pids)
                except queue.Full:
                    continue
            except queue.Full:
                continue
        if heartbeat_conn is not None:
            try:
                heartbeat_conn.send({"pids": len(pids), "ts": time.time()})
            except (BrokenPipeError, OSError):
                heartbeat_conn = None
        time.sleep(0.5)
