from __future__ import annotations

from PySide6.QtCore import Qt

from codex_widget import __version__
from codex_widget.ui import _enable_inactive_tooltips, _format_display_note, _version_menu_text


class _DummyWidget:
    def __init__(self) -> None:
        self.attributes = []

    def setAttribute(self, attribute, enabled=True) -> None:  # noqa: ANN001
        self.attributes.append((attribute, enabled))


def test_format_display_note_compacts_hook_status() -> None:
    assert _format_display_note('hook 工作中: UserPromptSubmit 00:12:47') == '钩子：提交提示词 00:12:47'


def test_format_display_note_keeps_multiple_notes_on_one_line() -> None:
    note = 'ChatGPT App 未运行\nhook 闲置: Stop 00:12:47'

    assert _format_display_note(note) == 'ChatGPT App 未运行 / 钩子：响应结束 00:12:47'


def test_format_display_note_keeps_active_summary() -> None:
    assert _format_display_note('待确认 × 1 · 工作 × 2') == '待确认 × 1 · 工作 × 2'


def test_format_display_note_translates_expired_event() -> None:
    assert _format_display_note('hook 工作中: UserPromptSubmitExpired') == '钩子：已自动恢复闲置'


def test_enable_inactive_tooltips_sets_qt_attribute() -> None:
    widget = _DummyWidget()

    _enable_inactive_tooltips(widget)

    assert widget.attributes == [(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)]


def test_version_menu_text_uses_package_version() -> None:
    assert _version_menu_text() == f'版本：{__version__}'
