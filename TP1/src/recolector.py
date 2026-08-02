import time

from src.procfs import list_pids


def run(task_queues, stop_event, heartbeat_conn=None):
    """Lista /proc y distribuye la lista de PIDs a todos los analizadores."""
    while not stop_event.is_set():
        pids = list_pids()
        for queue in task_queues.values():
            if queue.qsize() < 2:
                queue.put(pids)
        if heartbeat_conn is not None:
            try:
                heartbeat_conn.send({"pids": len(pids), "ts": time.time()})
            except (BrokenPipeError, OSError):
                pass
        time.sleep(0.5)
