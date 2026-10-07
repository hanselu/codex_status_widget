from __future__ import annotations

from collections.abc import Iterator

from PIL import Image

SCREEN_VID = 0x0416
SCREEN_PID = 0x90A1
SCREEN_WIDTH = 960
SCREEN_HEIGHT = 376
BAUD_RATE = 1_500_000
DATA_CHUNK_SIZE = 47

OPEN_SCREEN = bytes.fromhex("AA55AA550B000000")
TURN_OFF_SCREEN = bytes.fromhex("AA55AA550A000000")
FRAME_START = bytes.fromhex("AA55AA550500000004000F2F00040B00")
DATA_HEADER = bytes.fromhex("AA55AA5508000000")
FRAME_END = bytes.fromhex("AA55AA5506000000")


def prepare_image(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    if image.size != (SCREEN_WIDTH, SCREEN_HEIGHT):
        image = image.resize((SCREEN_WIDTH, SCREEN_HEIGHT), Image.Resampling.LANCZOS)
    return image


def encode_rgb565_le(image: Image.Image) -> bytes:
    rgb = image.convert("RGB").tobytes()
    encoded = bytearray(len(rgb) // 3 * 2)
    output_index = 0
    for input_index in range(0, len(rgb), 3):
        red, green, blue = rgb[input_index : input_index + 3]
        pixel = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        encoded[output_index] = pixel & 0xFF
        encoded[output_index + 1] = pixel >> 8
        output_index += 2
    return bytes(encoded)


def iter_data_packets(image_data: bytes) -> Iterator[bytes]:
    for offset in range(0, len(image_data), DATA_CHUNK_SIZE):
        payload = image_data[offset : offset + DATA_CHUNK_SIZE]
        yield DATA_HEADER + offset.to_bytes(4, "little") + payload
