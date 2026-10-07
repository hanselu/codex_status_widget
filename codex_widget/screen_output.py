from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import time

from PIL import Image
from PySide6.QtCore import QIODevice, QSaveFile
from PySide6.QtGui import QImage

from .models import CodexSnapshot
from .gem12_screen import Screen
from .screen_renderer import ScreenFrame, frame_from_snapshot, render_screen


def save_screen_image(image: QImage, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = QSaveFile(str(path))
    if not target.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(f'无法保存屏幕图片：{target.errorString()}')
    if not image.save(target, 'PNG'):
        target.cancelWriting()
        raise OSError('屏幕图片 PNG 编码失败')
    if not target.commit():
        raise OSError(f'无法更新屏幕图片：{target.errorString()}')


def pillow_image(image: QImage) -> Image.Image:
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    return Image.frombytes('RGB', (rgb.width(), rgb.height()), bytes(rgb.constBits()),
                           'raw', 'RGB', rgb.bytesPerLine())


class ScreenOutput:
    """主线程接收最新快照；单个后台线程保存图片并独占屏幕串口。"""

    def __init__(self, output_path: Path, port: str = '') -> None:
        self.output_path = output_path
        self.port = port
        self.status_text = '屏幕：等待推送'
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='gem12-screen')
        self._future: Future[str] | None = None
        self._sending: ScreenFrame | None = None
        self._sent: ScreenFrame | None = None
        self._latest: ScreenFrame | None = None
        self._image: QImage | None = None
        self._retry_at = 0.0
        self._screen: Screen | None = None
        self._closed = False

    def update(self, snapshot: CodexSnapshot) -> None:
        if self._closed:
            return
        if self._future is not None and self._future.done():
            try:
                port = self._future.result()
                self._sent = self._sending
                self.status_text = f'屏幕：已推送至 {port}'
            except Exception as exc:
                self.status_text = f'屏幕：{exc}（15秒后重试）'
                self._sent = None
                self._retry_at = time.monotonic() + 15
            self._future = None

        frame = frame_from_snapshot(snapshot)
        if frame != self._latest:
            self._image = render_screen(frame)
            self._latest = frame
        if self._future is None and frame != self._sent and time.monotonic() >= self._retry_at:
            self._sending = frame
            self._future = self._executor.submit(self._send, self._image)
            self.status_text = '屏幕：正在推送'

    def _send(self, image: QImage) -> str:
        try:
            save_screen_image(image, self.output_path)
            if self._screen is None:
                self._screen = Screen.connect(self.port or None)
            self._screen.show(pillow_image(image))
            return self._screen.port
        except Exception:
            self._disconnect()
            raise

    def _disconnect(self) -> None:
        screen, self._screen = self._screen, None
        if screen is not None:
            screen.close()

    def close(self, *, wait: bool = False) -> None:
        if not self._closed:
            self._closed = True
            # Close on the same worker after any current frame finishes.
            self._executor.submit(self._disconnect)
        self._executor.shutdown(wait=wait)
