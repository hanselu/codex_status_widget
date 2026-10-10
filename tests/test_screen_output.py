from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, get_ident
from unittest.mock import MagicMock

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from codex_widget.config import AppConfig
from codex_widget.models import CodexSnapshot, HookSignal, QuotaWindow
from codex_widget.screen_output import ScreenOutput, pillow_image, save_screen_image
from codex_widget.screen_renderer import frame_from_snapshot, render_screen
import main


@pytest.fixture(scope='module', autouse=True)
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def snapshot(*, dual=False, status='working', used=54):
    weekly = QuotaWindow(used_percent=used, window_minutes=10080,
                         resets_at=datetime(2026, 10, 12, 1, tzinfo=timezone.utc))
    return CodexSnapshot(
        status=status, status_text={'working': '工作中', 'waiting': '待确认', 'idle': '闲置中',
                                   'cooldown': '无额度', 'offline': '未运行'}[status],
        primary_title='周额度', primary=weekly, primary_text='', primary_visible=True,
        secondary_title='5小时', secondary=QuotaWindow(used_percent=28, window_minutes=300),
        secondary_text='', secondary_visible=dual, reset_text='', updated_text='12:00:00',
        hook_signal=HookSignal(status='working', working_count=3),
    )


def test_weekly_only_and_reverse_order_dual_windows():
    one = frame_from_snapshot(snapshot())
    assert [q.title for q in one.quotas] == ['周额度剩余']
    assert one.quotas[0].remaining == 46
    two = frame_from_snapshot(snapshot(dual=True))
    assert [q.title for q in two.quotas] == ['5 小时剩余', '周额度剩余']
    assert [q.remaining for q in two.quotas] == [72, 46]


def test_zero_unknown_and_no_quota_are_distinct():
    assert frame_from_snapshot(snapshot(used=100)).quotas[0].remaining == 0
    unknown = snapshot(used=None)
    assert frame_from_snapshot(unknown).quotas[0].title == '周额度剩余'
    assert frame_from_snapshot(unknown).quotas[0].remaining is None
    unknown.primary = QuotaWindow()
    assert frame_from_snapshot(unknown).quotas[0].title == '额度未读取'
    unknown.primary = QuotaWindow(used_percent=20, window_minutes=240)
    unknown.primary_title = '额度'
    assert frame_from_snapshot(unknown).quotas[0].title == '额度剩余'


@pytest.mark.parametrize('status', ['working', 'waiting', 'idle', 'cooldown', 'offline'])
@pytest.mark.parametrize('dual', [False, True])
def test_render_and_png_round_trip(tmp_path, status, dual):
    image = render_screen(frame_from_snapshot(snapshot(dual=dual, status=status)))
    assert (image.width(), image.height(), image.devicePixelRatio()) == (960, 376, 1)
    assert image.pixelColor(0, 0).name() == '#080f19'
    converted = pillow_image(image)
    assert converted.size == (960, 376)
    assert converted.getpixel((0, 0)) == (8, 15, 25)
    path = tmp_path / 'status.png'
    save_screen_image(image, path)
    with Image.open(path) as saved:
        assert saved.size == (960, 376)
        assert saved.convert('RGB').tobytes() == converted.tobytes()


def test_refresh_time_and_invisible_fraction_do_not_change_frame():
    original = snapshot(used=54.1)
    changed = replace(original, updated_text='12:00:03')
    changed.primary = replace(original.primary, used_percent=54.2)
    assert frame_from_snapshot(original) == frame_from_snapshot(changed)


@pytest.mark.parametrize('status', ['idle', 'working'])
def test_screen_shows_background_memory_separately(status):
    value = snapshot(status=status)
    value.hook_signal = HookSignal(status=status, working_count=int(status == 'working'),
                                   background_count=1)

    frame = frame_from_snapshot(value)

    assert frame.status == status
    assert '后台正在整理记忆' in frame.summary
    if status == 'idle':
        assert frame.summary == '后台正在整理记忆'
    else:
        assert frame.summary == '工作 1 · 待确认 0\n后台正在整理记忆'
    assert not render_screen(frame).isNull()


class FakeScreen:
    port = 'COM_TEST'

    def __init__(self):
        self.images = []
        self.threads = []
        self.closed = False

    def show(self, image):
        self.images.append(image.copy())
        self.threads.append(get_ident())

    def close(self):
        self.closed = True
        self.threads.append(get_ident())


@pytest.mark.parametrize('port', ['', 'COM3'])
def test_screen_once_uses_installed_library(tmp_path, monkeypatch, capsys, port):
    config = AppConfig.default()
    config.screen.port = port
    monkeypatch.setattr(main.AppConfig, 'load', lambda: config)
    monkeypatch.setattr(main, 'read_snapshot', lambda config: snapshot())
    monkeypatch.setattr('codex_widget.config.CONFIG_DIR', tmp_path)
    device = MagicMock()
    device.__enter__.return_value = device
    device.port = 'COM_TEST'
    device.show.return_value = 15360
    connect = MagicMock(return_value=device)
    monkeypatch.setattr('gem12_screen.Screen.connect', connect)

    assert main.screen_once() == 0

    connect.assert_called_once_with(port or None)
    device.show.assert_called_once()
    image = device.show.call_args.args[0]
    assert isinstance(image, Image.Image)
    assert image.size == (960, 376)
    device.__exit__.assert_called_once_with(None, None, None)
    assert (tmp_path / 'gem12-status.png').is_file()
    assert '已推送至 COM_TEST：960×376，15360 个数据块' in capsys.readouterr().out


def test_slow_push_keeps_ui_free_and_only_latest_pending_frame(tmp_path, monkeypatch):
    started, release = Event(), Event()
    device = FakeScreen()
    original_show = device.show

    def show(image):
        original_show(image)
        if len(device.images) == 1:
            started.set()
            assert release.wait(5)

    device.show = show
    connections = []

    def connect(port):
        connections.append(port)
        return device

    monkeypatch.setattr('codex_widget.screen_output.Screen.connect', connect)
    output = ScreenOutput(tmp_path / 'status.png')
    try:
        output.update(snapshot())
        assert started.wait(2)
        output.update(snapshot(status='idle'))
        output.update(snapshot(status='waiting'))
        assert len(device.images) == 1
        release.set()
        output._future.result(timeout=2)
        output.update(snapshot(status='waiting'))
        output._future.result(timeout=2)
        output.update(snapshot(status='waiting'))
        assert output._future is None
        assert len(device.images) == 2
        expected = pillow_image(render_screen(frame_from_snapshot(snapshot(status='waiting'))))
        assert device.images[-1].tobytes() == expected.tobytes()
        assert connections == [None]
        assert output.status_text == '屏幕：已推送至 COM_TEST'
    finally:
        release.set()
        output.close(wait=True)
    assert device.closed
    assert len(set(device.threads)) == 1
    assert device.threads[0] != get_ident()


def test_failure_preserves_png_and_retries_latest_frame_after_delay(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr('codex_widget.screen_output.time.monotonic', lambda: now[0])
    device = FakeScreen()
    calls = []

    def connect(port):
        calls.append(port)
        if len(calls) == 1:
            raise OSError('测试串口被占用')
        return device

    monkeypatch.setattr('codex_widget.screen_output.Screen.connect', connect)
    output = ScreenOutput(tmp_path / 'status.png', 'COM3')
    try:
        output.update(snapshot())
        with pytest.raises(OSError):
            output._future.result(timeout=2)
        output.update(snapshot())
        assert '15秒后重试' in output.status_text
        assert output.output_path.exists()
        now[0] = 14
        output.update(snapshot(status='idle'))
        assert len(calls) == 1
        now[0] = 15
        output.update(snapshot(status='idle'))
        output._future.result(timeout=2)
        output.update(snapshot(status='idle'))
        assert calls == ['COM3', 'COM3']
        assert output.status_text == '屏幕：已推送至 COM_TEST'
    finally:
        output.close(wait=True)


def test_failed_send_closes_connection_and_close_stops_new_work(tmp_path, monkeypatch):
    device = FakeScreen()

    def fail(image):
        raise OSError('设备断开')

    device.show = fail
    monkeypatch.setattr('codex_widget.screen_output.Screen.connect', lambda port: device)
    output = ScreenOutput(tmp_path / 'status.png')
    output.update(snapshot())
    with pytest.raises(OSError):
        output._future.result(timeout=2)
    assert device.closed
    output.close(wait=True)
    output.close(wait=True)
    output.update(snapshot(status='idle'))
    assert output._latest.status == 'working'


def test_screen_config_round_trip_and_old_config_default(tmp_path, monkeypatch):
    monkeypatch.setattr('codex_widget.config.CONFIG_DIR', tmp_path)
    monkeypatch.setattr('codex_widget.config.CONFIG_PATH', tmp_path / 'config.toml')
    (tmp_path / 'config.toml').write_text('[status]\nrefresh_interval_seconds = 5\n')
    config = AppConfig.load()
    assert config.screen.enabled is False
    config.screen.enabled = True
    config.screen.port = 'COM3'
    config.save()
    loaded = AppConfig.load()
    assert loaded.screen.enabled is True
    assert loaded.screen.port == 'COM3'
