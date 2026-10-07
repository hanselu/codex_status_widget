from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from codex_widget.gem12_screen import Screen, ScreenError
from codex_widget.gem12_screen._protocol import FRAME_END, FRAME_START, OPEN_SCREEN, TURN_OFF_SCREEN


class FakeSerial:
    def __init__(self, response: bytes = b"A") -> None:
        self.response = response
        self.is_open = True
        self.writes: list[bytes] = []
        self.closed = False

    def reset_input_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def read(self, size: int) -> bytes:
        return self.response[:size]

    def close(self) -> None:
        self.is_open = False
        self.closed = True


class ScreenTests(unittest.TestCase):
    @patch("codex_widget.gem12_screen.screen.serial.Serial")
    @patch("codex_widget.gem12_screen.screen._find_screen_port", return_value="COM3")
    def test_public_api_connects_and_sends_image(self, find_port, serial_class) -> None:
        connection = FakeSerial()
        serial_class.return_value = connection

        with Screen.connect() as screen:
            packet_count = screen.show(Image.new("RGB", (960, 376), "red"))

        self.assertEqual(screen.port, "COM3")
        self.assertEqual(packet_count, 15_360)
        self.assertEqual(connection.writes[0], OPEN_SCREEN)
        self.assertEqual(connection.writes[1], FRAME_START)
        self.assertEqual(connection.writes[-1], FRAME_END)
        self.assertTrue(connection.closed)
        find_port.assert_called_once_with(None)

    @patch("codex_widget.gem12_screen.screen.serial.Serial")
    @patch("codex_widget.gem12_screen.screen._find_screen_port", return_value="COM3")
    def test_connect_closes_port_when_ack_is_missing(self, find_port, serial_class) -> None:
        connection = FakeSerial(response=b"")
        serial_class.return_value = connection

        with self.assertRaisesRegex(ScreenError, "没有返回预期响应"):
            Screen.connect()

        self.assertTrue(connection.closed)

    @patch("codex_widget.gem12_screen.screen.serial.Serial")
    @patch("codex_widget.gem12_screen.screen._find_screen_port", return_value="COM3")
    def test_turn_off_can_connect_without_waking_screen(self, find_port, serial_class) -> None:
        connection = FakeSerial()
        serial_class.return_value = connection

        with Screen.connect(wake=False) as screen:
            screen.turn_off()

        self.assertEqual(connection.writes, [TURN_OFF_SCREEN])
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
