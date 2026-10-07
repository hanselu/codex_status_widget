from __future__ import annotations

import unittest

from PIL import Image

from codex_widget.gem12_screen._protocol import DATA_HEADER, encode_rgb565_le, iter_data_packets


class ProtocolTests(unittest.TestCase):
    def test_rgb565_is_little_endian(self) -> None:
        image = Image.new("RGB", (5, 1))
        image.putdata([(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255)])

        self.assertEqual(
            encode_rgb565_le(image),
            bytes.fromhex("0000 FFFF 00F8 E007 1F00"),
        )

    def test_data_packets_contain_little_endian_offsets(self) -> None:
        data = bytes(range(100))

        packets = list(iter_data_packets(data))

        self.assertEqual(len(packets), 3)
        self.assertEqual(packets[0], DATA_HEADER + bytes.fromhex("00000000") + data[:47])
        self.assertEqual(packets[1], DATA_HEADER + bytes.fromhex("2F000000") + data[47:94])
        self.assertEqual(packets[2], DATA_HEADER + bytes.fromhex("5E000000") + data[94:])


if __name__ == "__main__":
    unittest.main()
