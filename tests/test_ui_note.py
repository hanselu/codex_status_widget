from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from codex_widget import __version__
from codex_widget.ui import (
    CodexWidget,
    _enable_inactive_tooltips,
    _format_display_note,
    _quota_display_parts,
    _version_menu_text,
)


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


def test_quota_display_parts_separate_percentage_and_reset_time() -> None:
    assert _quota_display_parts('5小时：71% 04:22', '5小时') == ('71%', '04:22')
    assert _quota_display_parts('周额度：46% 7-12 16:23', '周额度') == ('46%', '7-12 16:23')
    assert _quota_display_parts('5小时：未读取 未知', '5小时') == ('未读取', '未知')
    assert _quota_display_parts('未读取', '5小时') == ('未读取', '')


def test_quota_rows_place_percentage_in_far_right_column() -> None:
    app = QApplication.instance() or QApplication([])
    widget = QWidget()

    CodexWidget._build_ui(widget)

    assert widget.primary_caption_label.text() == '5小时'
    assert widget.secondary_caption_label.text() == '周额度'
    assert widget.primary_caption_label.alignment() & Qt.AlignmentFlag.AlignLeft
    assert widget.secondary_caption_label.alignment() & Qt.AlignmentFlag.AlignLeft
    assert widget.primary_reset_label.alignment() & Qt.AlignmentFlag.AlignRight
    assert widget.secondary_reset_label.alignment() & Qt.AlignmentFlag.AlignRight
    assert widget.primary_percentage_label.alignment() & Qt.AlignmentFlag.AlignRight
    assert widget.secondary_percentage_label.alignment() & Qt.AlignmentFlag.AlignRight
    quota_layout = widget.card.layout().itemAt(1).layout()
    assert quota_layout is not None
    assert quota_layout.columnStretch(1) == 1
    assert quota_layout.columnStretch(2) == 0
    primary_percentage_index = quota_layout.indexOf(widget.primary_percentage_label)
    secondary_percentage_index = quota_layout.indexOf(widget.secondary_percentage_label)
    assert quota_layout.getItemPosition(primary_percentage_index)[1] == 2
    assert quota_layout.getItemPosition(secondary_percentage_index)[1] == 2

    widget.close()
    assert app is not None


def test_enable_inactive_tooltips_sets_qt_attribute() -> None:
    widget = _DummyWidget()

    _enable_inactive_tooltips(widget)

    assert widget.attributes == [(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)]


def test_version_menu_text_uses_package_version() -> None:
    assert _version_menu_text() == f'版本：{__version__}'
