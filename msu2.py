"""MSU2 USB display protocol and a small command line client."""
from __future__ import annotations

import argparse
import json
import logging
import re
import struct
import sys
import time
from dataclasses import asdict, dataclass

import serial
from serial.tools import list_ports
from standby_format import FRAME_COUNT, FRAME_SIZE
from asset_layout import IMAGE_BYTES, PAGE_BYTES, slot_for

HELLO = b"\x00MSNCN"
BANNER = re.compile(rb"\x00MSN([0-9]{2})")
logger = logging.getLogger(__name__)


class DeviceError(RuntimeError):
    pass


class DeviceTimeout(DeviceError):
    pass


class DeviceSessionReset(DeviceError):
    """The firmware returned to its initial banner/handshake state."""


class DeviceReplyError(DeviceError):
    """A reply boundary lost synchronization; only live tasks may retry."""


@dataclass(frozen=True)
class DeviceInfo:
    port: str
    protocol: str
    width: int
    height: int


def find_ports():
    """Only suggest matching USB devices; do not probe Bluetooth ports."""
    ports = [p for p in list_ports.comports()
             if (p.vid, p.pid) == (0x1A86, 0xFE0C)]
    if sys.platform == "darwin":
        names = {p.device for p in ports}
        ports = [p for p in ports if not (
            p.device.startswith("/dev/tty.")
            and p.device.replace("/dev/tty.", "/dev/cu.", 1) in names)]
        ports.sort(key=lambda p: (not p.device.startswith("/dev/cu."), p.device))
    return ports


def parse_descriptors(raw: bytes):
    records = {}
    offset = 0

    def cstring():
        nonlocal offset
        end = raw.find(b"\x00", offset)
        if end < 0:
            raise DeviceError("设备描述表不完整")
        result = raw[offset:end]
        offset = end + 1
        return result

    while offset < len(raw):
        name = cstring()
        if not name:
            break
        unit, family_raw = cstring(), cstring()
        if len(family_raw) != 1:
            raise DeviceError("未知设备描述格式")
        family = family_raw[0]
        kind = family // 32
        length = {0: 2, 1: 1, 2: 2, 3: family % 32}.get(kind)
        if length is None or offset + length > len(raw):
            raise DeviceError("不支持的设备描述")
        records[name.decode("ascii")] = (family, raw[offset:offset + length])
        offset += length
    return records


def encode_pixels(pixels: bytes) -> bytes:
    """Encode RGB565 bytes into the device's 256-byte staging packets."""
    if not pixels or len(pixels) % 2:
        raise ValueError("RGB565 数据必须包含完整像素")
    output = bytearray()
    for offset in range(0, len(pixels), 256):
        chunk = pixels[offset:offset + 256]
        used = len(chunk)
        chunk = chunk.ljust(256, b"\xff")
        for slot in range(64):
            output.extend((4, slot))
            output.extend(chunk[slot * 4:slot * 4 + 4])
        output.extend((2, 3, 8, used >> 8, used & 255, 0))
    # Each command 8 commits a block; this firmware does not need command 9.
    return bytes(output)


def encode_flash_page(page: int, data: bytes) -> bytes:
    """Encode one 256-byte page using the vendor's flash-write protocol."""
    if not 0 <= page < 4096:
        raise ValueError("Flash page is outside the 1 MiB device")
    if len(data) != 256:
        raise ValueError("Flash page data must contain exactly 256 bytes")
    packet = bytearray()
    for slot in range(64):
        packet.extend((4, slot))
        packet.extend(data[slot * 4:slot * 4 + 4])
    packet.extend((3, 3))
    packet.extend(page.to_bytes(3, "big"))
    packet.append(1)
    return bytes(packet)


def image_bytes(image) -> bytes:
    output = bytearray()
    raw = image.convert("RGB").tobytes()
    for red, green, blue in zip(raw[0::3], raw[1::3], raw[2::3]):
        rgb565 = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        output.extend(struct.pack(">H", rgb565))
    return bytes(output)


class Display:
    def __init__(self, port: str):
        self.port = port
        self.serial = None
        self.info = None
        self.last_activity = time.monotonic()
        self.recovery_count = 0
        self._pending_protocol = None
        self._display_payload = False

    def _read_exact(self, count: int, timeout=2.0) -> bytes:
        end = time.monotonic() + timeout
        data = bytearray()
        while len(data) < count and time.monotonic() < end:
            data.extend(self.serial.read(count - len(data)))
        if len(data) != count:
            raise DeviceTimeout("设备回复超时；请检查连接并关闭原厂软件")
        return bytes(data)

    def _write(self, data: bytes):
        view = memoryview(data)
        # Short writes preserve the device's six-byte command boundaries on
        # macOS CDC. Display-only paths can omit the historical delay after
        # stress validation; flash programming keeps its conservative pacing.
        paced = sys.platform == "darwin" and len(view) > 60
        pause = 0 if self._display_payload else .001
        while view:
            count = self.serial.write(view[:60] if paced else view)
            if not count:
                raise DeviceError("设备写入失败")
            view = view[count:]
            if paced and pause:
                time.sleep(pause)

    def _exchange(self, packet: bytes, timeout=2.0):
        self._write(packet)
        reply = self._read_reply(6, timeout=timeout)
        if reply[:2] != packet[:2]:
            raise DeviceReplyError(f"设备回复不匹配：{reply.hex(' ')}")
        self.last_activity = time.monotonic()
        return reply

    def _read_reply(self, count, timeout=2.0):
        reply = self._read_exact(count, timeout=timeout)
        # Display replies begin with two bytes, while the reset banner is six.
        # Only recognize banners at reply boundaries, never inside image data.
        if count < 6 and b'\x00MSN'.startswith(reply):
            reply += self._read_exact(6 - len(reply), timeout=timeout)
        match = BANNER.fullmatch(reply)
        if match:
            self._pending_protocol = match.group(1).decode()
            raise DeviceSessionReset(f'设备会话已重置（MSN{self._pending_protocol}），需要重新握手')
        return reply

    def _display_ack(self):
        reply = self._read_reply(2)
        if reply != bytes((2, 3)):
            raise DeviceReplyError(f"显示回复不匹配：{reply.hex(' ')}")
        # Some firmware returns only the two-byte prefix. Once a suffix starts,
        # drain all four bytes even if USB split them across multiple reads.
        suffix = self.serial.read(4)
        if suffix and len(suffix) < 4:
            self._read_exact(4 - len(suffix), timeout=.5)

    def ping(self):
        if self.info is None:
            raise DeviceError('请先连接屏幕')
        if (self.read_u16(0), self.read_u16(1)) != (self.info.width, self.info.height):
            raise DeviceError('设备尺寸通信检查失败')

    def recover(self):
        """Reconnect a read/display session. Never replay flash writes."""
        previous = self.info
        if self._pending_protocol is not None and self.serial is not None:
            # We already consumed the banner. Reuse it instead of waiting for
            # another broadcast or starting a second host session.
            protocol = self._pending_protocol
            self._pending_protocol = None
            try:
                current = self._handshake(protocol)
            except Exception:
                self.close()
                raise
        else:
            self.close()
            current = self.connect()
        if previous is not None and (
                current.protocol, current.width, current.height) != (
                previous.protocol, previous.width, previous.height):
            self.close()
            raise DeviceError('重连后设备规格改变，请重新连接并确认预览')
        self.recovery_count += 1
        logger.warning('Session recovered port=%s protocol=%s size=%sx%s count=%s',
                       self.port, current.protocol, current.width, current.height,
                       self.recovery_count)
        return current

    def live_operation(self, operation):
        """Recover once after session expiry, timeout or reply desynchronization.

        Only callers with idempotent reads/displays may use this wrapper.
        Erase/write commands always propagate errors without replaying them.
        """
        try:
            return operation()
        except (DeviceTimeout, DeviceSessionReset, DeviceReplyError) as exc:
            logger.warning('Recovering live operation port=%s error=%s', self.port, exc)
            self.recover()
            return operation()

    def read_u8(self, address: int):
        reply = self._exchange(bytes((0, 48, 0, address >> 8, address & 255, 0)))
        return reply[5]

    def read_u16(self, address: int):
        reply = self._exchange(bytes((0, 48, 32, address, 0, 0)))
        return int.from_bytes(reply[4:6], "big")

    def read_adc(self, channel=9):
        if self.serial is None or self.info is None:
            raise DeviceError("请先连接屏幕")
        if not 0 <= channel <= 15:
            raise ValueError("ADC channel must be between 0 and 15")
        reply = self._exchange(bytes((8,channel,0,0,0,0)))
        return int.from_bytes(reply[4:6],"big")

    def connect(self):
        if self.serial is not None:
            raise DeviceError("设备已经连接")
        try:
            self.serial = serial.Serial(self.port, 19200, timeout=0.1, write_timeout=3)
            banner = bytearray()
            # This firmware keeps its host session briefly after the port closes.
            # Wait for it to return to the banner state before the next handshake.
            deadline = time.monotonic() + 10
            match = None
            while time.monotonic() < deadline and len(banner) < 4096:
                banner.extend(self.serial.read(min(self.serial.in_waiting or 1, 256)))
                match = BANNER.search(banner)
                if match:
                    break
            if not match:
                raise DeviceError("没有收到 MSU2 设备标识；请退出原厂助手，必要时重新插入小屏")
            return self._handshake(match.group(1).decode())
        except Exception:
            self.close()
            raise

    def _handshake(self, protocol):
        self.serial.reset_input_buffer()
        self._write(HELLO)
        response = bytearray()
        deadline = time.monotonic() + 2
        while HELLO not in response and time.monotonic() < deadline:
            response.extend(self.serial.read(min(self.serial.in_waiting or 1, 256)))
        if HELLO not in response:
            raise DeviceError("设备握手失败")
        self.serial.reset_input_buffer()
        raw = bytes(self.read_u8(256 + i) for i in range(256))
        records = parse_descriptors(raw)
        sizes = []
        for name in ("Lcd_X", "Lcd_Y"):
            family, value = records[name]
            if family // 32 != 1:
                raise DeviceError("未知屏幕尺寸格式")
            sizes.append(self.read_u16(value[0]))
        width, height = sizes
        if not (1 <= width <= 320 and 1 <= height <= 320):
            raise DeviceError(f"屏幕尺寸异常：{width} × {height}")
        self.info = DeviceInfo(self.port, protocol, width, height)
        self._pending_protocol = None
        return self.info

    def show(self, image):
        if self.serial is None or self.info is None:
            raise DeviceError("请先连接屏幕")
        width, height = self.info.width, self.info.height
        if image.size != (width, height):
            raise ValueError(f"画面必须是 {width} × {height}")
        self._write(bytes((2, 0, 0, 0, 0, 0)))
        self._write(bytes((2, 1)) + struct.pack(">HH", width, height))
        self._write(bytes((2, 3, 7, 0, 0, 0)))
        self._display_ack()
        self._display_payload = True
        try:
            self._write(encode_pixels(image_bytes(image)))
        finally:
            self._display_payload = False
        # The final display packet has no acknowledgement. Read dimensions as a fence.
        if (self.read_u16(0), self.read_u16(1)) != (width, height):
            raise DeviceError("Display communication check failed")


    def read_flash_byte(self, address: int):
        if not 0 <= address < 1024 * 1024:
            raise ValueError("Flash address is outside the 1 MiB device")
        packet = bytes((3, 0)) + address.to_bytes(3, "big") + b"\x00"
        return self._exchange(packet)[5]

    def require_asset_device(self):
        if self.serial is None or self.info is None:
            raise DeviceError("请先连接屏幕")
        if (self.info.width, self.info.height) != FRAME_SIZE or self.info.protocol != "01":
            raise DeviceError("屏内素材目前支持已核对的 160 × 80 小屏")

    def read_asset(self, key, progress=None, cancelled=None):
        self.require_asset_device()
        slot = slot_for(key)
        return self.read_flash_region(slot.start_page * PAGE_BYTES, IMAGE_BYTES,
                                      progress, cancelled)

    def read_flash_region(self, address, length, progress=None, cancelled=None):
        self.require_asset_device()
        if (type(address) is not int or type(length) is not int or address < 0
                or length < 1 or address + length > 1024 * 1024):
            raise ValueError('闪存读取范围无效')
        data = bytearray()
        started_at = time.monotonic()
        for offset in range(length):
            if cancelled is not None and cancelled():
                # Cancellation is only checked between complete request/reply pairs.
                from assets import AssetCancelled
                raise AssetCancelled("已取消读取，屏内素材未改动")
            # Reading a byte is safe to repeat, including before the first
            # backup read after a long confirmation dialog. Writes never retry.
            data.append(self.live_operation(lambda: self.read_flash_byte(address + offset)))
            if progress is not None and (offset + 1) % PAGE_BYTES == 0:
                elapsed = time.monotonic() - started_at
                remaining = (length - offset - 1) * elapsed / (offset + 1)
                progress(f"正在读取闪存：{offset + 1}/{length} 字节，预计还需 {remaining / 60:.1f} 分钟…")
        return bytes(data)

    def write_asset(self, key, data, progress=None):
        self.require_asset_device()
        slot = slot_for(key)
        if not isinstance(data, bytes) or len(data) != IMAGE_BYTES:
            raise ValueError("素材必须是完整的 160 × 80 RGB565 数据")

        def report(message):
            if progress is not None:
                progress(message)

        report(f"正在擦除{slot.label}，请勿拔线…")
        self._exchange(bytes((3, 2)) + struct.pack(">HH", slot.start_page, slot.pages),
                       timeout=30)
        for offset in range(slot.pages):
            packet = encode_flash_page(slot.start_page + offset,
                                       data[offset * PAGE_BYTES:(offset + 1) * PAGE_BYTES])
            self._write(packet[:-6])
            self._exchange(packet[-6:], timeout=5)
            report(f"正在写入{slot.label}：{offset + 1}/{slot.pages} 页，请勿拔线…")
        report("写入完成，正在完整回读校验…")
        actual = self.read_asset(key, progress)
        if actual != data:
            raise DeviceError("素材完整回读校验失败；原图备份已保存，请保持连接后恢复")
        report(f"{slot.label}写入完成，完整回读校验通过")
        return {"slot": key, "pages": slot.pages, "verified_bytes": len(actual)}

    def show_asset(self, key):
        self.require_asset_device()
        slot = slot_for(key)
        self._write(bytes((2, 0, 0, 0, 0, 0)))
        self._write(bytes((2, 1)) + struct.pack(">HH", *FRAME_SIZE))
        # Vendor LCD_Photo: command 0 loads RGB565 from a page address.
        self._write(bytes((2, 3, 0, slot.start_page >> 8, slot.start_page & 255, 0)))
        self._display_ack()
        if (self.read_u16(0), self.read_u16(1)) != FRAME_SIZE:
            raise DeviceError("素材显示通信校验失败")

    def burn_offline(self, image, progress=None):
        """Keep the fixed-image API, using the same validated animation writer."""
        return self.burn_offline_frames((image,) * FRAME_COUNT, progress=progress)

    def burn_offline_frames(self, frames, progress=None):
        """Write exactly 36 prepared frames, leaving pages 3600..4095 intact."""
        if self.serial is None or self.info is None:
            raise DeviceError("请先连接屏幕")
        if (self.info.width, self.info.height) != FRAME_SIZE or self.info.protocol != "01":
            raise DeviceError("离线烧录目前仅支持已核对协议的 160 × 80 小屏")
        frames = tuple(frames)
        if len(frames) != FRAME_COUNT:
            raise ValueError("离线动画必须恰好包含 36 帧")
        # Finish validating and encoding every frame before the first erase.
        pixels_by_frame = []
        for frame in frames:
            if frame.size != FRAME_SIZE:
                raise ValueError("离线画面必须是 160 × 80")
            pixels = image_bytes(frame)
            if len(pixels) != 25600:
                raise DeviceError("离线图像大小与待机动画区不符")
            pixels_by_frame.append(pixels)
        pages_per_frame = 100
        page_count = pages_per_frame * FRAME_COUNT

        def report(message):
            if progress is not None:
                progress(message)

        report("正在擦除原待机动画，请勿拔线…")
        self._exchange(bytes((3, 2)) + struct.pack(">HH", 0, page_count), timeout=30)
        for frame_index, pixels in enumerate(pixels_by_frame):
            for offset in range(pages_per_frame):
                page = frame_index * pages_per_frame + offset
                data = pixels[offset * 256:(offset + 1) * 256]
                packet = encode_flash_page(page, data)
                self._write(packet[:-6])
                self._exchange(packet[-6:], timeout=5)
            report(f"正在烧录离线动画：{frame_index + 1}/{FRAME_COUNT} 帧，请勿拔线…")

        report("写入完成，正在抽样校验闪存…")
        samples = (0, 1, 255, 256, 12799, 12800, 25598, 25599)
        for frame_index, pixels in enumerate(pixels_by_frame):
            for offset in samples:
                address = frame_index * len(pixels) + offset
                if self.read_flash_byte(address) != pixels[offset]:
                    raise DeviceError("离线画面抽样校验失败，请保持连接后重新烧录")
        self.show(frames[0])
        return {"frames": FRAME_COUNT, "pages": page_count,
                "sampled_bytes": FRAME_COUNT * len(samples)}

    def close(self):
        if self.serial is not None:
            self.serial.close()
        self.serial = None
        self.info = None
        self._pending_protocol = None


def test_image(size):
    from PIL import Image, ImageDraw
    width, height = size
    image = Image.new("RGB", size, "#101824")
    draw = ImageDraw.Draw(image)
    colors = ["#ffffff", "#ffff00", "#00ffff", "#00ff00",
              "#ff00ff", "#ff0000", "#0000ff"]
    for i, color in enumerate(colors):
        draw.rectangle((i * width // 7, 0, (i + 1) * width // 7 - 1,
                        height // 2 - 1), fill=color)
    draw.text((8, height // 2 + 7), "HELLO MSU2", fill="#6ee7b7")
    draw.text((8, height - 14), f"{width} x {height} / USB OK", fill="white")
    return image


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="MSU2 小屏连接和显示测试")
    parser.add_argument("--port", help="例如 COM8 或 /dev/cu.usbmodem...")
    parser.add_argument("action", choices=["ports", "probe", "test"], nargs="?", default="probe")
    args = parser.parse_args()
    if args.action == "ports":
        print(json.dumps([{"port": p.device, "description": p.description}
                          for p in find_ports()], ensure_ascii=False, indent=2))
        return
    candidates = find_ports()
    port = args.port
    if not port:
        if len(candidates) != 1:
            parser.error("请用 --port 指定小屏串口")
        port = candidates[0].device
    display = Display(port)
    try:
        info = display.connect()
        print(json.dumps(asdict(info), ensure_ascii=False, indent=2))
        if args.action == "test":
            display.show(test_image((info.width, info.height)))
            print("测试画面已发送，设备通信检查通过。")
    except (DeviceError, serial.SerialException, KeyError, ValueError) as exc:
        parser.exit(1, f"连接或显示失败：{exc}\n")
    finally:
        display.close()


if __name__ == "__main__":
    main()
