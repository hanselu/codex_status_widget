from __future__ import annotations

from PySide6.QtCore import Qt

from codex_widget.ui import _enable_inactive_tooltips, _format_display_note


class _DummyWidget:
    def __init__(self) -> None:
        self.attributes = []

    def setAttribute(self, attribute, enabled=True) -> None:  # noqa: ANN001
        self.attributes.append((attribute, enabled))


def test_format_display_note_compacts_hook_status() -> None:
    assert _format_display_note('hook 工作中: UserPromptSubmit 00:12:47') == '钩子：提交提示词 00:12:47'


def test_format_display_note_keeps_multiple_notes_on_one_line() -> None:
    note = 'Codex App 未运行\nhook 闲置: Stop 00:12:47'

    assert _format_display_note(note) == 'Codex App 未运行 / 钩子：响应结束 00:12:47'


def test_format_display_note_keeps_active_summary() -> None:
    assert _format_display_note('等待 1 · 工作 2 · 响应 1') == '等待 1 · 工作 2 · 响应 1'


def test_format_display_note_translates_expired_event() -> None:
    assert _format_display_note('hook 工作中: UserPromptSubmitExpired') == '钩子：已自动恢复闲置'


def test_enable_inactive_tooltips_sets_qt_attribute() -> None:
    widget = _DummyWidget()

    _enable_inactive_tooltips(widget)

    assert widget.attributes == [(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)]
