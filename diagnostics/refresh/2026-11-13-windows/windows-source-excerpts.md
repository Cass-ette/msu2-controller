# Measured Windows source excerpts

Read-only excerpts from uncommitted Windows msu2.py; not production replacements.

File SHA-256: 2990752e4a8a0e38d81b66d4b0d3604353b92e27604032f9046742fbb76e10e7

Original line numbers follow. Source differs from repository main.


## encode_pixels (line 82)

~~~python
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
~~~


## image_bytes (line 115)

~~~python
def image_bytes(image) -> bytes:
    output = bytearray()
    raw = image.convert("RGB").tobytes()
    for red, green, blue in zip(raw[0::3], raw[1::3], raw[2::3]):
        rgb565 = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        output.extend(struct.pack(">H", rgb565))
    return bytes(output)
~~~


## _read_exact (line 130)

~~~python
    def _read_exact(self, count: int, timeout=2.0) -> bytes:
        end = time.monotonic() + timeout
        data = bytearray()
        while len(data) < count and time.monotonic() < end:
            data.extend(self.serial.read(count - len(data)))
        if len(data) != count:
            raise DeviceError("设备回复超时；请检查连接并关闭原厂软件")
        return bytes(data)
~~~


## _write (line 139)

~~~python
    def _write(self, data: bytes):
        view = memoryview(data)
        # Bulk frame writes caused mismatched replies on the tested macOS device.
        # Pace six-byte commands in chunks below the full-speed USB packet limit.
        paced = sys.platform == "darwin" and len(view) > 60
        while view:
            count = self.serial.write(view[:60] if paced else view)
            if not count:
                raise DeviceError("设备写入失败")
            view = view[count:]
            if paced:
                time.sleep(0.001)
~~~


## _exchange (line 152)

~~~python
    def _exchange(self, packet: bytes, timeout=2.0):
        self._write(packet)
        reply = self._read_exact(6, timeout=timeout)
        if reply[:2] != packet[:2]:
            banner = BANNER.fullmatch(reply)
            if banner is not None:
                if self.info is not None and banner.group(1).decode() != self.info.protocol:
                    raise DeviceError("设备协议标识已改变，请断开后重新连接")
                raise SessionExpired("设备空闲超时，已返回待连接状态")
            raise DeviceError(f"设备回复不匹配：{reply.hex(' ')}")
        return reply
~~~


## _read_command (line 175)

~~~python
    def _read_command(self, packet):
        # Only idempotent reads can be retried after a confirmed startup banner.
        try:
            return self._exchange(packet)
        except SessionExpired:
            self._handshake()
            return self._exchange(packet)
~~~


## keep_alive (line 183)

~~~python
    def keep_alive(self):
        if self.serial is None or self.info is None:
            raise DeviceError("请先连接屏幕")
        dimensions = (self.read_u16(0), self.read_u16(1))
        if dimensions != (self.info.width, self.info.height):
            raise DeviceError("设备尺寸已改变，请重新连接")
~~~


## read_u16 (line 194)

~~~python
    def read_u16(self, address: int):
        reply = self._read_command(bytes((0, 48, 32, address, 0, 0)))
        return int.from_bytes(reply[4:6], "big")
~~~


## show (line 239)

~~~python
    def show(self, image):
        if self.serial is None or self.info is None:
            raise DeviceError("请先连接屏幕")
        width, height = self.info.width, self.info.height
        if image.size != (width, height):
            raise ValueError(f"画面必须是 {width} × {height}")
        self.keep_alive()
        self._write(bytes((2, 0, 0, 0, 0, 0)))
        self._write(bytes((2, 1)) + struct.pack(">HH", width, height))
        self._write(bytes((2, 3, 7, 0, 0, 0)))
        reply = self._read_exact(2)
        self.serial.read(4)  # Display acknowledgements may be shorter than register replies.
        if reply != bytes((2, 3)):
            raise DeviceError(f"Invalid display acknowledgement: {reply.hex(' ')}")
        self._write(encode_pixels(image_bytes(image)))
        # The final display packet has no acknowledgement. Read dimensions as a fence.
        if (self.read_u16(0), self.read_u16(1)) != (width, height):
            raise DeviceError("Display communication check failed")
~~~
