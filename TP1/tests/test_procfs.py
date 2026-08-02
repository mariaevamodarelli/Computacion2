import os
import tempfile
import unittest

from src.procfs import decode_signal_mask, infer_fd_type, map_segments, parse_stat_line


class ProcfsParsingTests(unittest.TestCase):
    def test_parse_stat_line_with_spaces_in_command(self):
        line = (
            "1234 (mi proceso test) S 1 2 3 4 5 6 7 8 9 10 "
            "11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 "
            "27 28 29 30 31 32 33 34 35 36 37 38 39 40 41"
        )

        parsed = parse_stat_line(line)

        self.assertEqual(parsed["pid"], 1234)
        self.assertEqual(parsed["comm"], "mi proceso test")
        self.assertEqual(parsed["state"], "S")
        self.assertEqual(parsed["utime"], 11)
        self.assertEqual(parsed["stime"], 12)
        self.assertEqual(parsed["priority"], 15)
        self.assertEqual(parsed["nice"], 16)

    def test_decode_signal_mask(self):
        self.assertEqual(decode_signal_mask("0000000000000002"), ["SIGINT"])

    def test_infer_fd_type(self):
        self.assertEqual(infer_fd_type("socket:[123]"), "socket")
        self.assertEqual(infer_fd_type("pipe:[456]"), "pipe")
        self.assertEqual(infer_fd_type("/tmp/archivo.txt"), "file")

    def test_map_segments_groups_basic_regions(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc_dir = os.path.join(tmp, "100")
            os.makedirs(proc_dir)
            with open(os.path.join(proc_dir, "maps"), "w", encoding="utf-8") as f:
                f.write("00400000-00401000 r-xp 00000000 00:00 0 /bin/demo\n")
                f.write("00600000-00602000 rw-p 00000000 00:00 0 [heap]\n")
                f.write("00700000-00701000 rw-p 00000000 00:00 0 [stack]\n")

            segments = map_segments(100, tmp)

            self.assertEqual(segments["text"], 4)
            self.assertEqual(segments["heap"], 8)
            self.assertEqual(segments["stack"], 4)


if __name__ == "__main__":
    unittest.main()