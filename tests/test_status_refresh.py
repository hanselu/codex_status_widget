from __future__ import annotations

from threading import Event
import json

import pytest
from PySide6.QtWidgets import QApplication

from codex_widget.config import AppConfig
from codex_widget.models import HookSignal, QuotaSnapshot, QuotaWindow
from codex_widget.snapshot import CodexSnapshotReader
from codex_widget.ui import CodexWidget


@pytest.fixture
def widget_factory(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    config = AppConfig.default()
    config.codex.sessions_dir = tmp_path / 'sessions'
    config.hook.events_path = tmp_path / 'events.jsonl'
    config.status.refresh_interval_seconds = 60
    reader = CodexSnapshotReader(config.codex.sessions_dir, config.hook.events_path)
    reader.hook_reader.read_signal = lambda: HookSignal(status='working')
    monkeypatch.setattr('codex_widget.ui.CodexSnapshotReader', lambda **kwargs: reader)
    monkeypatch.setattr('codex_widget.ui.CONFIG_DIR', tmp_path)
    monkeypatch.setattr(CodexWidget, '_build_tray', lambda self: None)
    monkeypatch.setattr(CodexWidget, '_apply_snapshot', lambda self, snapshot: None)
    widgets = []

    def create(read_quota):
        reader.quota_reader.read_quota = read_quota
        widget = CodexWidget(config)
        widgets.append(widget)
        return widget, reader

    yield create
    for widget in widgets:
        widget._shutdown_quota_worker()
        widget._quota_executor.shutdown(wait=True)
        app.aboutToQuit.disconnect(widget._shutdown_quota_worker)
        widget.close()
        widget.deleteLater()
    app.processEvents()


def test_slow_quota_does_not_block_status_or_start_duplicate_queries(widget_factory):
    started = Event()
    release = Event()
    calls = []

    def slow_quota():
        calls.append(1)
        started.set()
        assert release.wait(5), '额度查询不应阻塞主线程'
        return QuotaSnapshot(primary=QuotaWindow(used_percent=25, window_minutes=300))

    try:
        widget, reader = widget_factory(slow_quota)
        assert started.wait(2)
        assert widget.timer.interval() == 3000
        assert widget._last_snapshot.status == 'working'
        reader.hook_reader.read_signal = lambda: HookSignal(status='idle')
        widget._refresh_status()
        assert widget._last_snapshot.status == 'idle'
        assert calls == [1]

        release.set()
        widget._quota_future.result(timeout=2)
        widget._refresh_status()
        assert widget._last_snapshot.primary.used_percent == 25
        widget._refresh_status()
        assert calls == [1]

        # The explicit refresh action still requests fresh quota immediately.
        widget.refresh()
        widget._quota_future.result(timeout=2)
        assert calls == [1, 1]
    finally:
        release.set()


def test_failed_quota_keeps_previous_value_and_status_responsive(widget_factory):
    def failed_quota():
        raise OSError('simulated offline')

    widget, reader = widget_factory(failed_quota)
    widget._quota = QuotaSnapshot(primary=QuotaWindow(used_percent=40, window_minutes=300))
    with pytest.raises(OSError):
        widget._quota_future.result(timeout=2)
    reader.hook_reader.read_signal = lambda: HookSignal(status='waiting')
    widget._refresh_status()
    assert widget._last_snapshot.status == 'waiting'
    assert widget._last_snapshot.primary.used_percent == 40
    assert widget._quota_future is None


def test_screen_receives_same_live_snapshot_and_closes_with_widget(widget_factory):
    widget, reader = widget_factory(lambda: QuotaSnapshot())
    received = []

    class Output:
        status_text = '屏幕：测试已推送'
        closed = False

        def update(self, snapshot):
            received.append(snapshot)

        def close(self):
            self.closed = True

    output = Output()
    widget._screen_output = output
    reader.hook_reader.read_signal = lambda: HookSignal(status='waiting', waiting_count=1)
    widget._refresh_status()
    assert received[-1] is widget._last_snapshot
    assert received[-1].status == 'waiting'
    assert widget.screen_status_action.text() == output.status_text
    widget._shutdown_quota_worker()
    assert output.closed


def test_screen_toggle_persists_and_releases_output(widget_factory, monkeypatch):
    widget, reader = widget_factory(lambda: QuotaSnapshot())
    saves = []
    monkeypatch.setattr(AppConfig, 'save', lambda config: saves.append(config.screen.enabled))
    outputs = []

    class Output:
        status_text = '屏幕：测试已推送'

        def __init__(self, path, port):
            self.received = []
            self.closed = False
            outputs.append(self)

        def update(self, snapshot):
            self.received.append(snapshot)

        def close(self):
            self.closed = True

    monkeypatch.setattr('codex_widget.ui.ScreenOutput', Output)
    widget.screen_action.setChecked(True)
    assert saves == [True]
    assert outputs[0].received == [widget._last_snapshot]
    widget.screen_action.setChecked(False)
    assert saves == [True, False]
    assert outputs[0].closed
    assert widget._screen_output is None


def test_json_file_follows_live_status_and_cached_quota(widget_factory, tmp_path):
    widget, reader = widget_factory(lambda: QuotaSnapshot())
    path = tmp_path / 'status.json'
    assert json.loads(path.read_text(encoding='utf-8'))['status'] == 'working'

    widget._quota_future.result(timeout=2)
    widget._refresh_status()
    widget._quota = QuotaSnapshot(primary=QuotaWindow(used_percent=40, window_minutes=300))
    reader.hook_reader.read_signal = lambda: HookSignal(status='waiting', waiting_count=1)
    widget._refresh_status()
    payload = json.loads(path.read_text(encoding='utf-8'))

    assert payload['status'] == widget._last_snapshot.status == 'waiting'
    assert payload['tasks']['waiting_count'] == 1
    assert payload['quotas'][0]['remaining_percent'] == 60


def test_json_write_failure_does_not_block_status_or_screen(widget_factory, monkeypatch, caplog):
    widget, reader = widget_factory(lambda: QuotaSnapshot())
    received = []

    class Output:
        status_text = '屏幕：测试已推送'

        def update(self, snapshot):
            received.append(snapshot)

        def close(self):
            pass

    def fail_save(*args):
        raise OSError('simulated write error')

    widget._screen_output = Output()
    monkeypatch.setattr('codex_widget.ui.save_status_json', fail_save)
    reader.hook_reader.read_signal = lambda: HookSignal(status='idle')
    widget._refresh_status()

    assert widget._last_snapshot.status == 'idle'
    assert received[-1] is widget._last_snapshot
    assert '状态 JSON 导出失败' in caplog.text
