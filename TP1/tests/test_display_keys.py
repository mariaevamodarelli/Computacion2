import unittest
import sys
import types


fake_curses = types.SimpleNamespace(
    A_BOLD=1,
    A_REVERSE=2,
    KEY_UP=259,
    KEY_DOWN=258,
    echo=lambda: None,
    noecho=lambda: None,
    error=Exception,
)
sys.modules.setdefault("curses", fake_curses)

from src import display


class FakeInterval:
    def __init__(self, value):
        self.value = value


class FakeStopEvent:
    def __init__(self):
        self.stopped = False

    def set(self):
        self.stopped = True


class FakeScreen:
    def __init__(self, key, typed=""):
        self.key = key
        self.typed = typed
        self.timeout_value = None

    def getch(self):
        return self.key

    def getmaxyx(self):
        return (24, 80)

    def getstr(self, _y, _x, _max_len):
        return self.typed.encode("utf-8")

    def timeout(self, value):
        self.timeout_value = value

    def addstr(self, *_args):
        return None


def intervals():
    return {name: FakeInterval(2.0) for name, _title, _letter, _min_interval in display.VIEWS}


class DisplayKeyTests(unittest.TestCase):
    def test_number_and_letter_change_view(self):
        state = display.DisplayState()
        display.handle_key(FakeScreen(ord("2")), state, intervals(), FakeStopEvent())
        self.assertEqual(state.view_name, "memoria")

        display.handle_key(FakeScreen(ord("g")), state, intervals(), FakeStopEvent())
        self.assertEqual(state.view_name, "sistema")

    def test_q_requests_shutdown(self):
        stop_event = FakeStopEvent()
        display.handle_key(FakeScreen(ord("q")), display.DisplayState(), intervals(), stop_event)
        self.assertTrue(stop_event.stopped)

    def test_help_toggle(self):
        state = display.DisplayState()
        display.handle_key(FakeScreen(ord("h")), state, intervals(), FakeStopEvent())
        self.assertTrue(state.show_help)

    def test_filters(self):
        state = display.DisplayState()
        display.handle_key(FakeScreen(ord("/"), "python"), state, intervals(), FakeStopEvent())
        self.assertEqual(state.filter_cmd, "python")

        display.handle_key(FakeScreen(ord("u"), "maria"), state, intervals(), FakeStopEvent())
        self.assertEqual(state.filter_user, "maria")

    def test_enter_pins_selected_pid(self):
        state = display.DisplayState()
        state.last_rows = [{"pid": 10}, {"pid": 20}]
        state.selected = 1
        display.handle_key(FakeScreen(10), state, intervals(), FakeStopEvent())
        self.assertEqual(state.pinned_pid, 20)

    def test_sort_and_interval_keys(self):
        state = display.DisplayState()
        all_intervals = intervals()

        display.handle_key(FakeScreen(ord("c")), state, all_intervals, FakeStopEvent())
        self.assertEqual(state.sort_name, "rss")

        display.handle_key(FakeScreen(ord("+")), state, all_intervals, FakeStopEvent())
        self.assertEqual(all_intervals[state.view_name].value, 1.5)

        display.handle_key(FakeScreen(ord("-")), state, all_intervals, FakeStopEvent())
        self.assertEqual(all_intervals[state.view_name].value, 2.0)

    def test_config_respects_minimum_intervals(self):
        state = display.DisplayState()
        all_intervals = intervals()
        old_load_config = display.load_config
        display.load_config = lambda: {
            "intervalos": {
                "resumen": 0.1,
                "fds": 0.1,
                "senales": 0.1,
            },
            "filtro_comando": "python",
            "filtro_usuario": "maria",
            "orden": "pid",
        }
        try:
            display.apply_config(all_intervals, state)
        finally:
            display.load_config = old_load_config

        self.assertEqual(all_intervals["resumen"].value, 0.5)
        self.assertEqual(all_intervals["fds"].value, 2.0)
        self.assertEqual(all_intervals["senales"].value, 5.0)
        self.assertEqual(state.filter_cmd, "python")
        self.assertEqual(state.filter_user, "maria")
        self.assertEqual(state.sort_name, "pid")


if __name__ == "__main__":
    unittest.main()
