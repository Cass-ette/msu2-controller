"""Mac serial selection and USB diagnostics, without Mac hardware."""
import plistlib
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image
import doctor
import features
import msu2


def port(name, vid=0x1A86, pid=0xFE0C):
    return SimpleNamespace(device=name, vid=vid, pid=pid, description="screen")


class MacTests(unittest.TestCase):
    def test_callout_selection_and_vid_filter(self):
        ports=[port("/dev/tty.usbmodem123"),port("/dev/cu.usbmodem123"),
               port("/dev/cu.Bluetooth",None,None),port("/dev/cu.other",0x1A86,0x7523)]
        with patch.object(msu2.sys,"platform","darwin"), patch.object(msu2.list_ports,"comports",return_value=ports):
            self.assertEqual([p.device for p in msu2.find_ports()],["/dev/cu.usbmodem123"])

    def test_tty_only_is_retained(self):
        with patch.object(msu2.sys,"platform","darwin"), patch.object(msu2.list_ports,"comports",return_value=[port("/dev/tty.usbmodem123")]):
            self.assertEqual(len(msu2.find_ports()),1)

    def test_windows_detection_is_retained(self):
        with patch.object(msu2.sys,"platform","win32"), patch.object(msu2.list_ports,"comports",return_value=[port("COM8"),port("COM3",1,2)]):
            self.assertEqual([p.device for p in msu2.find_ports()],["COM8"])

    def test_ioreg_nested_device_match(self):
        tree=[{"IORegistryEntryChildren":[{"idVendor":0x1A86,"idProduct":0xFE0C,"USB Product Name":"MSU2"},{"idVendor":0x1A86,"idProduct":0x7523}]}]
        with patch.object(doctor.subprocess,"run",return_value=SimpleNamespace(stdout=plistlib.dumps(tree))):
            result=doctor.inspect_usb()
        self.assertTrue(result["available"])
        self.assertEqual(len(result["matches"]),1)
        self.assertEqual(result["matches"][0]["name"],"MSU2")

    def test_ioreg_failure_is_reported(self):
        with patch.object(doctor.subprocess,"run",side_effect=OSError("not available")):
            self.assertFalse(doctor.inspect_usb()["available"])

    def test_advice_separates_usb_and_serial(self):
        report={"platform":"darwin","python_supported":True,"modules":{"serial":{"installed":True}},"screen_ports":[],"usb":{"available":True,"matches":[]}}
        self.assertIn("USB 设备树没有找到"," ".join(doctor.advice(report)))
        report["usb"]["matches"]=[{"name":"MSU2"}]
        self.assertIn("USB 已识别"," ".join(doctor.advice(report)))

    def collect(self, ports, probe=False, selected=None, failure=None):
        device=MagicMock()
        device.connect.return_value=msu2.DeviceInfo("/dev/cu.usbmodem123","01",160,80)
        if failure:
            device.connect.side_effect=failure
        with patch.object(doctor,"module_status",return_value={"installed":True}), patch.object(doctor.platform,"platform",return_value="macOS simulated"), patch.object(doctor,"inspect_usb",return_value={"available":True,"matches":[]}), patch.object(msu2.sys,"platform","darwin"), patch.object(msu2.list_ports,"comports",return_value=ports), patch.object(msu2,"Display",return_value=device) as factory:
            result=doctor.collect(probe=probe,port=selected)
        return result,device,factory

    def test_diagnosis_without_probe_never_opens_device(self):
        result,device,factory=self.collect([port("/dev/cu.usbmodem123")])
        self.assertEqual(result["screen_ports"],["/dev/cu.usbmodem123"])
        factory.assert_not_called()

    def test_probe_always_releases_port_and_does_not_send_or_burn(self):
        for failure in (None,OSError("busy")):
            result,device,factory=self.collect([port("/dev/cu.usbmodem123")],True,failure=failure)
            self.assertEqual(result["probe"]["ok"],failure is None)
            device.close.assert_called_once()
            device.show.assert_not_called()
            device.burn_offline.assert_not_called()

    def test_multiple_or_nonmatching_devices_are_not_probed(self):
        ports=[port("/dev/cu.usbmodem123"),port("/dev/cu.usbmodem456")]
        result,device,factory=self.collect(ports,True)
        self.assertIn("skipped",result["probe"])
        factory.assert_not_called()
        result,device,factory=self.collect(ports,True,"/dev/cu.Bluetooth")
        self.assertFalse(result["probe"]["ok"])
        factory.assert_not_called()

    def test_mac_capture_scope_and_permission_error(self):
        with patch.object(features.sys,"platform","darwin"), patch.object(features,"screen_capture_access",return_value={'granted':True}), patch.object(features.ImageGrab,"grab",return_value=Image.new("RGB",(320,200))) as grab:
            self.assertEqual(features.capture_desktop((160,80)).size,(160,80))
            grab.assert_called_once_with()
            with self.assertRaisesRegex(features.ContentError,"支持主屏幕"):
                features.capture_desktop((160,80),"整个桌面")
        with patch.object(features.sys,"platform","darwin"), patch.object(features,"screen_capture_access",return_value={'granted':True}), patch.object(features.ImageGrab,"grab",side_effect=OSError("denied")):
            with self.assertRaisesRegex(features.ContentError,"允许终端或 Python"):
                features.capture_desktop((160,80))


if __name__=="__main__":
    unittest.main(verbosity=2)
