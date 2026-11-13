"""Local USB/serial/environment diagnostics; never writes display pixels or flash."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib
import json
import platform
import plistlib
import subprocess
import sys
from platform_support import connection_failure, screen_capture_access

VID, PID = 0x1A86, 0xFE0C


def parse_usb_tree(tree):
    matches = []

    def walk(value):
        if isinstance(value, dict):
            if (value.get("idVendor"), value.get("idProduct")) == (VID, PID):
                matches.append({
                    "name": value.get("USB Product Name") or value.get("USBProductName")
                    or value.get("IORegistryEntryName", "MSU2 USB device"),
                    "vid": VID, "pid": PID,
                })
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(tree)
    return matches


def inspect_usb():
    try:
        result = subprocess.run(
            ["/usr/sbin/ioreg", "-p", "IOUSB", "-a", "-l"],
            capture_output=True, timeout=15, check=True)
        return {"available": True, "matches": parse_usb_tree(plistlib.loads(result.stdout))}
    except (OSError, subprocess.SubprocessError, ValueError, plistlib.InvalidFileException) as exc:
        return {"available": False, "error": str(exc)}


def module_status(name):
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", None)
        if name == "serial":
            version = module.VERSION
        if name == "tkinter":
            version = str(module.TkVersion)
        return {"installed": True, "version": version}
    except Exception as exc:
        return {"installed": False, "error": str(exc)}


def advice(report):
    tips = []
    mac = report["platform"] == "darwin"
    if not report["python_supported"]:
        tips.append("请安装 Python 3.11 或更新版本。")
    if any(not item["installed"] for item in report["modules"].values()):
        tips.append("请先运行 Setup.command；Tkinter 缺失时建议使用 python.org 的 macOS Python 安装包。")
    ports = report.get("screen_ports", [])
    if mac and not ports:
        usb = report.get("usb", {})
        if usb.get("available") and usb.get("matches"):
            tips.append("USB 已识别到 1A86:FE0C，但尚未找到对应串口。请重新插拔并查看系统信息的 USB 页面；当前固件声明标准 CDC ACM 接口，先核对系统串口绑定。")
        elif usb.get("available"):
            tips.append("Mac 的 USB 设备树没有找到小屏。请先解锁 Mac、允许配件连接，检查数据线/转接头，并尝试直接连接。")
        else:
            tips.append("USB 查询未完成，请在“系统信息 → USB”查看是否出现 VID 1A86、PID FE0C。")
        tips.append("部分搭载 Apple 芯片的 Mac 笔记本会要求允许 USB 配件连接；请查看系统设置的隐私与安全性。")
        tips.append("这块屏在 Windows 使用系统 usbser 驱动；不要仅凭 VID 1A86 就认定需要 CH340 驱动。")
    elif not ports:
        tips.append("未找到匹配的 USB 串口，请检查连接。")
    if ports and report.get("probe", {}).get("ok") is False:
        kind = report['probe'].get('error_kind')
        if kind == 'permission':
            tips.append("串口存在，但运行环境拒绝访问（权限或沙箱限制）。请从已获授权的终端运行 Diagnose.command；不能据此判断驱动缺失或串口被占用。")
        elif kind == 'busy':
            tips.append("串口已被占用：请退出其他小屏程序或串口工具后重试。")
        else:
            tips.append("串口存在但握手或通信失败：请检查其他程序占用、数据线和设备状态，必要时重新插拔。")
    if ports and "probe" not in report:
        tips.append("已找到匹配串口；加 --probe 可以检查 MSU2 握手和尺寸。")
    if report.get("probe", {}).get("ok"):
        tips.append("设备识别、MSU2 握手和尺寸读取均已通过，可打开 Start.command。")
    if report.get("probe", {}).get("skipped"):
        tips.append("发现多个匹配设备，请用 --port 指定一个串口后再运行 --probe。")
    return tips


def collect(probe=False, port=None):
    report = {
        "platform": sys.platform, "system": platform.platform(),
        "python": sys.version.split()[0], "python_executable": sys.executable,
        "python_supported": sys.version_info >= (3, 11),
        "modules": {name: module_status(name) for name in ("serial", "PIL", "psutil", "tkinter")},
        "screen_ports": [], "serial_ports": [],
    }
    if sys.platform == "darwin":
        report["usb"] = inspect_usb()
        report['screen_capture'] = screen_capture_access()
    if report["modules"]["serial"]["installed"]:
        try:
            from serial.tools import list_ports
            from msu2 import Display, find_ports
            ports = find_ports()
            report["screen_ports"] = [item.device for item in ports]
            report["serial_ports"] = [
                {"port": item.device, "vid": item.vid, "pid": item.pid,
                 "description": item.description} for item in list_ports.comports()]
            if probe and ports:
                candidates = [item.device for item in ports]
                selected = port or (candidates[0] if len(candidates) == 1 else None)
                if selected is None:
                    report["probe"] = {"skipped": "multiple devices"}
                elif selected not in candidates:
                    report["probe"] = {"ok": False, "error": "指定串口不在匹配设备列表中，未打开"}
                else:
                    device = Display(selected)
                    try:
                        report["probe"] = {"ok": True, "device": asdict(device.connect())}
                    except Exception as exc:
                        report["probe"] = {"ok": False, "error": str(exc),
                                           'error_kind': connection_failure(exc)}
                    finally:
                        device.close()
        except Exception as exc:
            report["serial_error"] = str(exc)
    report["advice"] = advice(report)
    return report


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="MSU2 USB / 串口 / Python 环境诊断")
    parser.add_argument("--probe", action="store_true", help="仅握手和读取尺寸，不传图或烧录")
    parser.add_argument("--port", help="多个匹配设备时指定一个 /dev/cu.usbmodem…")
    args = parser.parse_args()
    report = collect(probe=args.probe, port=args.port)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
