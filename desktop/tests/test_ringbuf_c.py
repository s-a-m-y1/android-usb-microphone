"""C ring buffer unit test runner: compiles and executes test_ringbuf.c."""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "desktop" / "pipewire" / "test_ringbuf.c"
BIN = ROOT / "build" / "test_ringbuf"


@unittest.skipUnless(SRC.exists(), "test_ringbuf.c not found")
class TestCRingBuffer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gcc = shutil.which("gcc")
        if gcc is None:
            raise unittest.SkipTest("gcc not available")
        BIN.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([gcc, "-O2", "-Wall", "-o", str(BIN), str(SRC)],
                       check=True)

    def test_c_ring_buffer_suite_passes(self):
        proc = subprocess.run([str(BIN)], capture_output=True, text=True,
                              timeout=30)
        self.assertEqual(proc.returncode, 0,
                         f"C tests failed:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("ALL TESTS PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
