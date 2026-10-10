from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from codex_widget import __version__
from codex_widget.models import CodexSnapshot, HookSignal, QuotaWindow
from codex_widget.ui import (
    MIN_WIDGET_HEIGHT,
    CodexWidget,
    _enable_inactive_tooltips,
    _format_display_note,
    _quota_display_parts,
    _tray_quota_number,
    _version_menu_text,
)


class _DummyWidget:
    def __init__(self) -> None:
        self.attributes = []

    def setAttribute(self, attribute, enabled=True) -> None:  # noqa: ANN001
        self.attributes.append((attribute, enabled))


class _TrayStub:
    def __init__(self) -> None:
        self.tooltip = ''
        self.icons = []

    def setToolTip(self, text: str) -> None:
        self.tooltip = text

    def setIcon(self, icon) -> None:  # noqa: ANN001
        self.icons.append(icon)


def _snapshot(
    *,
    primary_title: str,
    primary: QuotaWindow,
    primary_text: str,
    primary_visible: bool,
    secondary_title: str = '',
    secondary: QuotaWindow | None = None,
    secondary_text: str = '',
    secondary_visible: bool = False,
) -> CodexSnapshot:
    return CodexSnapshot(
        status='idle',
        status_text='闲置中',
        primary_title=primary_title,
        primary=primary,
        primary_text=primary_text,
        primary_visible=primary_visible,
        secondary_title=secondary_title,
        secondary=secondary or QuotaWindow(),
        secondary_text=secondary_text,
        secondary_visible=secondary_visible,
        reset_text='',
        updated_text='12:00:00',
        hook_signal=HookSignal(status='idle'),
    )


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


def test_tray_quota_prefers_five_hour_then_weekly() -> None:
    five_hour = QuotaWindow(used_percent=26, window_minutes=300)
    weekly = QuotaWindow(used_percent=40, window_minutes=10080)
    snapshot = _snapshot(
        primary_title='周额度', primary=weekly, primary_text='周额度：60% 未知',
        primary_visible=True, secondary_title='5小时', secondary=five_hour,
        secondary_text='5小时：74% 未知', secondary_visible=True,
    )
    assert _tray_quota_number(snapshot) == '74'

    snapshot.secondary = QuotaWindow(window_minutes=300)
    assert _tray_quota_number(snapshot) == '60'

    snapshot.primary = QuotaWindow()
    assert _tray_quota_number(snapshot) == '--'


def test_tray_icon_updates_when_quota_or_status_changes() -> None:
    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    widget.tray = _TrayStub()
    widget._tray_icon_key = None
    snapshot = _snapshot(
        primary_title='5小时', primary=QuotaWindow(used_percent=26, window_minutes=300),
        primary_text='5小时：74% 未知', primary_visible=True,
    )

    CodexWidget._set_tray_icon(widget, snapshot)
    assert widget._tray_icon_key == ('idle', '74')
    assert widget.tray.icons[0].availableSizes()
    CodexWidget._set_tray_icon(widget, snapshot)
    assert len(widget.tray.icons) == 1

    snapshot.primary.used_percent = 30
    CodexWidget._set_tray_icon(widget, snapshot)
    assert widget._tray_icon_key == ('idle', '70')
    snapshot.status = 'working'
    CodexWidget._set_tray_icon(widget, snapshot)
    assert widget._tray_icon_key == ('working', '70')
    assert len(widget.tray.icons) == 3
    widget.close()
    assert app is not None


def test_quota_rows_place_percentage_in_far_right_column() -> None:
    app = QApplication.instance() or QApplication([])
    widget = QWidget()

    CodexWidget._build_ui(widget)

    assert widget.primary_caption_label.text() == '额度'
    assert widget.secondary_caption_label.text() == '额外额度'
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


def test_resize_counts_requested_note_visibility_before_first_show() -> None:
    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    widget.setFixedSize(220, 132)
    CodexWidget._build_ui(widget)
    widget._note_display_text = '工作 × 1'
    widget.note_label.setText(widget._note_display_text)
    widget.note_label.setStyleSheet('font-size: 32px;')
    widget.note_label.setVisible(True)
    widget._max_available_height = lambda: 600
    widget._keep_inside_screen = lambda: None

    assert widget.note_label.isVisible() is False
    assert widget.note_label.isHidden() is False
    CodexWidget._resize_to_content(widget)
    initial_height = widget.height()
    assert initial_height > MIN_WIDGET_HEIGHT

    widget.show()
    app.processEvents()
    CodexWidget._resize_to_content(widget)

    assert widget.height() == initial_height
    widget.close()


def test_resize_excludes_explicitly_hidden_note_before_first_show() -> None:
    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    widget.setFixedSize(220, 132)
    CodexWidget._build_ui(widget)
    widget._note_display_text = ''
    widget.note_label.setVisible(False)
    widget.secondary_caption_label.setVisible(False)
    widget.secondary_reset_label.setVisible(False)
    widget.secondary_percentage_label.setVisible(False)
    widget._max_available_height = lambda: 600
    widget._keep_inside_screen = lambda: None

    CodexWidget._resize_to_content(widget)

    assert widget.height() == MIN_WIDGET_HEIGHT
    widget.close()
    assert app is not None


def test_idle_background_hint_is_visible_and_clears_on_completion() -> None:
    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    widget.setFixedSize(220, 132)
    CodexWidget._build_ui(widget)
    widget._max_available_height = lambda: 600
    widget._keep_inside_screen = lambda: None
    widget._resize_to_content = lambda: CodexWidget._resize_to_content(widget)
    widget._set_tray_icon = lambda status: None
    widget.tray = _TrayStub()
    value = _snapshot(primary_title='周额度', primary=QuotaWindow(),
                      primary_text='周额度：未读取 未知', primary_visible=True)
    value.note = '后台正在整理记忆'
    value.detail = '后台记忆整理\n- 最近活动：00:23:30 · Bash（调用已结束）'
    value.hook_signal = HookSignal(status='idle', background_count=1)

    CodexWidget._apply_snapshot(widget, value)
    assert widget.title_label.text() == '闲置中'
    assert '#31c46b' in widget.status_dot.styleSheet()
    assert widget.note_label.isHidden() is False
    assert widget.note_label.text() == '后台正在整理记忆'
    assert value.detail in widget.tray.tooltip

    value.note = value.detail = ''
    value.hook_signal = HookSignal(status='idle')
    CodexWidget._apply_snapshot(widget, value)
    assert widget.note_label.isHidden() is True
    assert '后台' not in widget.tray.tooltip
    widget.close()
    assert app is not None


def test_apply_snapshot_switches_quota_rows_between_single_and_double() -> None:
    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    widget.setFixedSize(220, 132)
    CodexWidget._build_ui(widget)
    widget._note_display_text = ''
    widget._max_available_height = lambda: 600
    widget._keep_inside_screen = lambda: None
    widget._resize_to_content = lambda: CodexWidget._resize_to_content(widget)
    widget._set_tray_icon = lambda status: None
    widget.tray = _TrayStub()

    weekly_only = _snapshot(
        primary_title='周额度',
        primary=QuotaWindow(used_percent=3, window_minutes=10080),
        primary_text='周额度：97% 7-21 20:22',
        primary_visible=True,
        secondary_title='额外额度',
        secondary_text='额外额度：50% 7-22 12:00',
    )
    CodexWidget._apply_snapshot(widget, weekly_only)
    single_row_height = widget.height()

    assert widget.primary_caption_label.text() == '周额度'
    assert widget.primary_percentage_label.text() == '97%'
    assert widget.primary_reset_label.text() == '7-21 20:22'
    assert widget.primary_caption_label.isHidden() is False
    assert widget.secondary_caption_label.isHidden() is True
    assert widget.secondary_reset_label.isHidden() is True
    assert widget.secondary_percentage_label.isHidden() is True
    assert '周额度：97% 7-21 20:22' in widget.tray.tooltip
    assert '额外额度' not in widget.tray.tooltip

    legacy = _snapshot(
        primary_title='5小时',
        primary=QuotaWindow(used_percent=25, window_minutes=300),
        primary_text='5小时：75% 04:22',
        primary_visible=True,
        secondary_title='周额度',
        secondary=QuotaWindow(used_percent=40, window_minutes=10080),
        secondary_text='周额度：60% 7-21 20:22',
        secondary_visible=True,
    )
    CodexWidget._apply_snapshot(widget, legacy)
    double_row_height = widget.height()

    assert widget.primary_caption_label.text() == '5小时'
    assert widget.secondary_caption_label.text() == '周额度'
    assert widget.secondary_caption_label.isHidden() is False
    assert widget.secondary_reset_label.isHidden() is False
    assert widget.secondary_percentage_label.isHidden() is False
    assert double_row_height > single_row_height

    CodexWidget._apply_snapshot(widget, weekly_only)

    assert widget.secondary_caption_label.isHidden() is True
    assert widget.height() == single_row_height
    widget.close()
    assert app is not None


def test_enable_inactive_tooltips_sets_qt_attribute() -> None:
    widget = _DummyWidget()

    _enable_inactive_tooltips(widget)

    assert widget.attributes == [(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)]


def test_version_menu_text_uses_package_version() -> None:
    assert _version_menu_text() == f'版本：{__version__}'
