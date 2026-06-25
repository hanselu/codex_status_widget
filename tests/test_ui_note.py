from __future__ import annotations

from codex_widget.ui import _format_display_note


def test_format_display_note_compacts_hook_status() -> None:
    assert _format_display_note('hook 工作中: UserPromptSubmit 00:12:47') == '钩子：提交提示词 00:12:47'


def test_format_display_note_keeps_multiple_notes_on_one_line() -> None:
    note = 'Codex App 未运行\nhook 闲置: Stop 00:12:47'

    assert _format_display_note(note) == 'Codex App 未运行 / 钩子：响应结束 00:12:47'


def test_format_display_note_translates_expired_event() -> None:
    assert _format_display_note('hook 工作中: UserPromptSubmitExpired') == '钩子：提示词事件过期'
