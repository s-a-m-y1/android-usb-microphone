"""Tests for adb `devices -l` parsing and the AdbDevice model."""

import unittest

from aum.adb.adb import AdbDevice, DeviceState, _parse_devices

SAMPLE = """* daemon not running; starting now at tcp:5037
* daemon started successfully
List of devices attached
0I74325I271005CA       device usb:1-6 product:RMX3760 model:RMX3760 device:RE58C2 transport_id:2
ABC123                 unauthorized usb:1-7 product:x model:Pixel_3
XYZ789                 offline
"""


class TestParseDevices(unittest.TestCase):
    def test_parses_all_states(self):
        devs = _parse_devices(SAMPLE)
        self.assertEqual([d.state for d in devs],
                         [DeviceState.AUTHORIZED, DeviceState.UNAUTHORIZED,
                          DeviceState.OFFLINE])

    def test_parses_model_and_product(self):
        devs = _parse_devices(SAMPLE)
        self.assertEqual(devs[0].serial, "0I74325I271005CA")
        self.assertEqual(devs[0].model, "RMX3760")
        self.assertEqual(devs[0].product, "RMX3760")
        self.assertEqual(devs[1].model, "Pixel_3")

    def test_empty(self):
        self.assertEqual(_parse_devices("List of devices attached\n"), [])
        self.assertEqual(_parse_devices(""), [])

    def test_usable_filter(self):
        devs = _parse_devices(SAMPLE)
        self.assertTrue(devs[0].usable)
        self.assertFalse(devs[1].usable)
        self.assertFalse(devs[2].usable)

    def test_display_name(self):
        devs = _parse_devices(SAMPLE)
        self.assertEqual(devs[0].display_name, "RMX3760")
        self.assertIn("unauthorized", devs[1].display_name)

    def test_garbage_lines_skipped(self):
        weird = "List of devices attached\n\nsome junk\nonlyonefield\n"
        self.assertEqual(_parse_devices(weird), [])

    def test_unknown_state_kept_out(self):
        weird = "List of devices attached\nS1 weirdblob\n"
        self.assertEqual(_parse_devices(weird), [])


class TestAdbDevice(unittest.TestCase):
    def test_display_name_fallback(self):
        d = AdbDevice(serial="s", state=DeviceState.AUTHORIZED)
        self.assertEqual(d.display_name, "Android Device")


if __name__ == "__main__":
    unittest.main()
