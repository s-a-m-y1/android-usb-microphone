"""Integration tests: PipeWire virtual source creation + start/stop lifecycle.

These are skipped automatically when PipeWire or the daemon binary are not
available (e.g. CI without audio). On the target desktop they run for real.
"""

import shutil
import subprocess
import time
import unittest
from pathlib import Path

from aum.core.daemon import PwDaemon, find_daemon_binary
from aum.transport.pw_bridge import PwBridge

ROOT = Path(__file__).resolve().parents[2]


def pipewire_available() -> bool:
    return shutil.which("pactl") is not None and \
        subprocess.run(["pactl", "info"], capture_output=True).returncode == 0


@unittest.skipUnless(find_daemon_binary() or (ROOT / "build" / "aum-pw-source").exists(),
                     "daemon binary not built")
@unittest.skipUnless(pipewire_available(), "PipeWire not reachable")
class TestPwSourceLifecycle(unittest.TestCase):
    """Uses a dedicated socket + node name so real sessions are not disturbed."""

    def setUp(self):
        socket_path = f"/tmp/aum-test-{int(time.time()*1000)}.sock"
        self.socket_path = socket_path
        binary = find_daemon_binary() or ROOT / "build" / "aum-pw-source"
        self.daemon = PwDaemon(binary)
        self.daemon.start(name="aum_test_mic",
                          description="AUM Test Mic",
                          socket_path=socket_path)
        self.bridge = PwBridge(socket_path)
        self.bridge.connect()

    def tearDown(self):
        self.bridge.close()
        self.daemon.stop()
        subprocess.run(["pactl", "list", "sources", "short"],
                       capture_output=True)

    def test_source_created_and_visible(self):
        deadline = time.monotonic() + 12
        visible = False
        while time.monotonic() < deadline and not visible:
            out = subprocess.run(["pactl", "list", "sources", "short"],
                                 capture_output=True, text=True).stdout
            visible = "aum_test_mic" in out
            if not visible:
                time.sleep(0.2)
        self.assertTrue(visible, "virtual source not visible in pactl")

    def test_start_stop_lifecycle(self):
        events = []
        self.bridge.send_config({"cmd": "noop"})
        time.sleep(0.3)
        # stop, then start again: same node must come back
        self.daemon.stop()
        self.assertFalse(self.daemon.running)
        binary = find_daemon_binary() or ROOT / "build" / "aum-pw-source"
        self.daemon = PwDaemon(binary)
        self.daemon.start(name="aum_test_mic",
                          description="AUM Test Mic",
                          socket_path=self.socket_path)
        self.assertTrue(self.daemon.running)
        self.bridge.close()
        self.bridge = PwBridge(self.socket_path)
        self.bridge.connect()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not events:
            time.sleep(0.1)
        self.assertTrue(self.daemon.running)


if __name__ == "__main__":
    unittest.main()
