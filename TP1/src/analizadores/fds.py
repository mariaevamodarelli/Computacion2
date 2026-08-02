from src.analizadores.base import analyzer_loop
from src.procfs import fd_info


def run(task_queue, result_queue, interval_value, stop_event):
    def analyze(pids):
        data = {}
        for pid in pids:
            item = fd_info(pid)
            if item:
                data[pid] = item
        return data

    analyzer_loop("fds", task_queue, result_queue, interval_value, stop_event, analyze)
