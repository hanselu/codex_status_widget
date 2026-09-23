from __future__ import annotations

from threading import Event

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
