from src.analizadores.base import analyzer_loop
from src.procfs import system_info


def run(task_queue, result_queue, interval_value, stop_event, snapshot):
    previous_cpu = {}

    def analyze(_pids):
        resumen = snapshot.get("resumen", {}).get("data", [])
        return system_info(previous_cpu, resumen)

    analyzer_loop("sistema", task_queue, result_queue, interval_value, stop_event, analyze)
