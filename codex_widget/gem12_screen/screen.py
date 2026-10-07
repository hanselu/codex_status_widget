from __future__ import annotations

from pathlib import Path
from types import TracebackType

import serial
from PIL import Image
from serial.tools import list_ports

from ._protocol import (
    BAUD_RATE,
    FRAME_END,
    FRAME_START,
    OPEN_SCREEN,
    SCREEN_HEIGHT,
    SCREEN_PID,
    SCREEN_VID,
    SCREEN_WIDTH,
    TURN_OFF_SCREEN,
    encode_rgb565_le,
    iter_data_packets,
    prepare_image,
)


class ScreenError(RuntimeError):
    """屏幕连接或画面发送失败。"""


def _find_screen_port(requested_port: str | None = None) -> str:
    matches = [
        port.device
        for port in list_ports.comports()
        if port.vid == SCREEN_VID and port.pid == SCREEN_PID
    ]
    if not matches:
        raise ScreenError(f"未找到 USB 设备 VID_{SCREEN_VID:04X}&PID_{SCREEN_PID:04X}")

    if requested_port is not None:
        for device in matches:
            if device.casefold() == requested_port.casefold():
                return device
        raise ScreenError(f"{requested_port} 不是目标屏幕串口；检测到：{', '.join(matches)}")

    if len(matches) > 1:
        raise ScreenError(f"检测到多个目标屏幕串口，请指定端口：{', '.join(matches)}")
    return matches[0]


class Screen:
    """AOOSTAR GEM12 屏幕的高层控制接口。"""

    def __init__(self, port: str, connection: serial.Serial) -> None:
        self.port = port
        self._serial = connection

    @classmethod
    def connect(cls, port: str | None = None, *, wake: bool = True) -> Screen:
        """自动寻找并连接屏幕；默认同时点亮屏幕。"""
        selected_port = _find_screen_port(port)
        try:
            connection = serial.Serial(
                selected_port,
                baudrate=BAUD_RATE,
                timeout=1,
                write_timeout=10,
            )
        except serial.SerialException as exc:
            raise ScreenError(f"无法打开屏幕串口 {selected_port}：{exc}") from exc

        screen = cls(selected_port, connection)
        try:
            if wake:
                screen.wake()
        except Exception:
            screen.close()
            raise
        return screen

    def __enter__(self) -> Screen:
        if not self.is_connected:
            raise ScreenError("屏幕连接已经关闭")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def is_connected(self) -> bool:
        """串口连接是否仍然打开。"""
        return self._serial.is_open

    def close(self) -> None:
        """关闭串口连接，不关闭屏幕或清除当前画面。"""
        if self._serial.is_open:
            self._serial.close()

    def wake(self) -> None:
        """发送开屏命令，并要求设备返回 ASCII ``A``。"""
        self._send_control_command(OPEN_SCREEN, "开屏")

    def turn_off(self) -> None:
        """关闭屏幕，并要求设备返回 ASCII ``A``。"""
        self._send_control_command(TURN_OFF_SCREEN, "关屏")

    def _send_control_command(self, command: bytes, action: str) -> None:
        self._require_connection()
        try:
            self._serial.reset_input_buffer()
            self._write_exact(command)
            self._serial.flush()
            response = self._serial.read(16)
        except serial.SerialException as exc:
            raise ScreenError(f"{action}命令发送失败：{exc}") from exc
        if b"A" not in response:
            response_text = response.hex(" ") or "无响应"
            raise ScreenError(f"{action}时屏幕没有返回预期响应 41，实际为：{response_text}")

    def show(self, source: str | Path | Image.Image) -> int:
        """显示图片并返回发送的数据块数量。"""
        self._require_connection()
        image = self._load_image(source)
        image_data = encode_rgb565_le(prepare_image(image))
        expected_size = SCREEN_WIDTH * SCREEN_HEIGHT * 2
        if len(image_data) != expected_size:
            raise ScreenError(f"图像编码长度异常：{len(image_data)}，预期 {expected_size}")

        try:
            self._write_exact(FRAME_START)
            packet_count = 0
            for packet in iter_data_packets(image_data):
                self._write_exact(packet)
                packet_count += 1
            self._write_exact(FRAME_END)
            self._serial.flush()
        except serial.SerialException as exc:
            raise ScreenError(f"画面发送失败：{exc}") from exc
        return packet_count

    def _load_image(self, source: str | Path | Image.Image) -> Image.Image:
        if isinstance(source, Image.Image):
            return source
        try:
            with Image.open(source) as image:
                return image.convert("RGB")
        except (OSError, ValueError) as exc:
            raise ScreenError(f"无法读取图片 {source}：{exc}") from exc

    def _require_connection(self) -> None:
        if not self.is_connected:
            raise ScreenError("屏幕连接已经关闭")

    def _write_exact(self, data: bytes) -> None:
        sent = 0
        while sent < len(data):
            count = self._serial.write(data[sent:])
            if count is None or count <= 0:
                raise ScreenError("串口写入未取得进展")
            sent += count
