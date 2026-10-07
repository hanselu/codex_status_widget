from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QFontMetrics, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import CONFIG_DIR, AppConfig
from .codex_app import codex_app_is_running
from .hook_installer import install_hooks, read_hook_setup_status
from .models import CodexSnapshot, QuotaSnapshot, StatusName
from .reset_credits import (
    RESET_CREDITS_TIMEOUT_MS,
    RESET_CREDITS_URL,
    ResetCreditsError,
    format_reset_credits,
    parse_reset_credits_response,
    read_access_token,
)
from .snapshot import CodexSnapshotReader
from .screen_output import ScreenOutput


STATUS_COLORS: dict[StatusName, str] = {
    'idle': '#31c46b',
    'working': '#4aa3ff',
    'waiting': '#ff9f1c',
    'cooldown': '#ff5a5f',
    'offline': '#ff5a5f',
}

MIN_WIDGET_HEIGHT = 64
HOOK_EVENT_LABELS = {
    'UserPromptSubmit': '提交提示词',
    'PreToolUse': '工具调用前',
    'PostToolUse': '工具调用后',
    'PermissionRequest': '待确认',
    'Stop': '响应结束',
    'TaskComplete': '任务完成',
    'UserPromptSubmitExpired': '已自动恢复闲置',
    'PreToolUseExpired': '已自动恢复闲置',
    'PermissionRequestExpired': '已自动恢复闲置',
    'SubagentStartExpired': '已自动恢复闲置',
    'ActiveExpired': '已自动恢复闲置',
    'SessionStart': '会话开始',
    'SubagentStart': '子任务开始',
    'SubagentStop': '子任务结束',
    'PreCompact': '压缩前',
    'PostCompact': '压缩后',
    'WidgetManualIdle': '手动标记闲置',
}


class CodexWidget(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.reader = CodexSnapshotReader(
            sessions_dir=self.config.codex.sessions_dir,
            hook_events_path=self.config.hook.events_path,
            hook_stale_after_minutes=self.config.hook.stale_after_minutes,
            hook_max_events_to_read=self.config.hook.max_events_to_read,
            fallback_working_window_seconds=self.config.status.working_window_seconds,
            codex_app_running=codex_app_is_running,
            hook_setup_status_reader=read_hook_setup_status,
        )
        self._drag_offset: QPoint | None = None
        self._last_snapshot: CodexSnapshot | None = None
        self._quota = QuotaSnapshot()
        self._quota_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='widget-quota')
        self._quota_future: Future[QuotaSnapshot] | None = None
        self._next_quota_refresh = 0.0
        self._tray_icon_key: tuple[StatusName, str] | None = None
        self._note_display_text = ''
        self._network_manager = QNetworkAccessManager(self)
        self._reset_credits_reply: QNetworkReply | None = None
        self._screen_output: ScreenOutput | None = None
        if self.config.screen.enabled:
            self._screen_output = ScreenOutput(CONFIG_DIR / 'gem12-status.png', self.config.screen.port)

        self._build_window()
        self._build_ui()
        self._build_menu()
        self._build_tray()
        self._restore_position()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        # Existing configurations often use 60 seconds to limit quota requests.
        # Keep status responsive independently of that slower network operation.
        self.timer.start(min(self.config.status.refresh_interval_seconds, 3) * 1000)
        QApplication.instance().aboutToQuit.connect(self._shutdown_quota_worker)
        self.refresh()

    def _build_window(self) -> None:
        self.setWindowTitle('Codex 状态')
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        _enable_inactive_tooltips(self)
        self.setWindowOpacity(self.config.ui.opacity)
        self.setFixedWidth(self.config.ui.width)
        self.setFixedHeight(self.config.ui.height)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName('card')
        self.card.setStyleSheet(
            '''
            QFrame#card {
                background-color: rgba(42, 43, 48, 238);
                border: 1px solid rgba(255, 255, 255, 36);
                border-radius: 10px;
            }
            QLabel {
                color: #e8e8ea;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 12px;
            }
            QLabel#titleLabel {
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#noteLabel {
                color: #aeb0b8;
                font-size: 11px;
            }
            '''
        )
        root.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(5)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.status_dot = QLabel(self.card)
        self.status_dot.setFixedSize(13, 13)
        top_row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel('Codex 状态', self.card)
        self.title_label.setObjectName('titleLabel')
        top_row.addWidget(self.title_label, 1)
        layout.addLayout(top_row)

        quota_layout = QGridLayout()
        quota_layout.setContentsMargins(0, 0, 0, 0)
        quota_layout.setHorizontalSpacing(8)
        quota_layout.setVerticalSpacing(layout.spacing())
        quota_layout.setColumnStretch(1, 1)

        self.primary_caption_label = QLabel('额度', self.card)
        self.primary_reset_label = QLabel('', self.card)
        self.primary_percentage_label = QLabel('未读取', self.card)
        self.secondary_caption_label = QLabel('额外额度', self.card)
        self.secondary_reset_label = QLabel('', self.card)
        self.secondary_percentage_label = QLabel('未读取', self.card)
        self.primary_caption_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.primary_reset_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.primary_percentage_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.secondary_caption_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.secondary_reset_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.secondary_percentage_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        quota_layout.addWidget(self.primary_caption_label, 0, 0)
        quota_layout.addWidget(self.primary_reset_label, 0, 1)
        quota_layout.addWidget(self.primary_percentage_label, 0, 2)
        quota_layout.addWidget(self.secondary_caption_label, 1, 0)
        quota_layout.addWidget(self.secondary_reset_label, 1, 1)
        quota_layout.addWidget(self.secondary_percentage_label, 1, 2)

        self.note_label = QLabel('', self.card)
        self.note_label.setObjectName('noteLabel')
        self.note_label.setWordWrap(False)

        layout.addLayout(quota_layout)
        layout.addWidget(self.note_label)
        _enable_inactive_tooltips(
            self.card,
            self.status_dot,
            self.title_label,
            self.primary_caption_label,
            self.primary_reset_label,
            self.primary_percentage_label,
            self.secondary_caption_label,
            self.secondary_reset_label,
            self.secondary_percentage_label,
            self.note_label,
        )

    def _build_menu(self) -> None:
        self.menu = QMenu(self)

        self.refresh_action = QAction('刷新', self)
        self.refresh_action.triggered.connect(self.refresh)
        self.menu.addAction(self.refresh_action)

        self.reset_credits_action = QAction('查询重置额度', self)
        self.reset_credits_action.triggered.connect(self.query_reset_credits)
        self.menu.addAction(self.reset_credits_action)

        self.mark_idle_action = QAction('标记为闲置', self)
        self.mark_idle_action.triggered.connect(self.mark_idle)
        self.menu.addAction(self.mark_idle_action)

        self.lock_action = QAction(self._lock_text(), self)
        self.lock_action.triggered.connect(self.toggle_lock)
        self.menu.addAction(self.lock_action)

        self.install_hook_action = QAction('添加钩子到 Codex', self)
        self.install_hook_action.triggered.connect(self.install_hook_to_codex)
        self.menu.addAction(self.install_hook_action)

        self.open_sessions_action = QAction('打开 sessions 目录', self)
        self.open_sessions_action.triggered.connect(self.open_sessions_dir)
        self.menu.addAction(self.open_sessions_action)

        self.open_state_action = QAction('打开状态目录', self)
        self.open_state_action.triggered.connect(self.open_state_dir)
        self.menu.addAction(self.open_state_action)

        self.menu.addSeparator()
        self.screen_action = QAction('推送至 GEM12 屏幕', self)
        self.screen_action.setCheckable(True)
        self.screen_action.setChecked(self.config.screen.enabled)
        self.screen_action.toggled.connect(self.toggle_screen_output)
        self.menu.addAction(self.screen_action)
        self.screen_status_action = QAction('屏幕：未启用', self)
        self.screen_status_action.setEnabled(False)
        self.menu.addAction(self.screen_status_action)

        self.menu.addSeparator()

        self.version_action = QAction(_version_menu_text(), self)
        self.version_action.setEnabled(False)
        self.menu.addAction(self.version_action)

        self.quit_action = QAction('退出', self)
        self.quit_action.triggered.connect(QApplication.quit)
        self.menu.addAction(self.quit_action)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip('Codex 状态')
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._set_tray_icon(None)
        self.tray.show()

    def refresh(self) -> None:
        self._next_quota_refresh = 0.0
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._quota_future is not None and self._quota_future.done():
            try:
                self._quota = self._quota_future.result()
            except Exception:
                # Keep the last quota if the background read unexpectedly fails.
                # Status must remain usable even when quota cannot be obtained.
                pass
            self._quota_future = None

        now = time.monotonic()
        if self._quota_future is None and now >= self._next_quota_refresh:
            self._quota_future = self._quota_executor.submit(self.reader.quota_reader.read_quota)
            self._next_quota_refresh = now + max(60, self.config.status.refresh_interval_seconds)

        snapshot = self.reader.read_snapshot(quota=self._quota)
        self._last_snapshot = snapshot
        self._apply_snapshot(snapshot)
        if self._screen_output is not None:
            self._screen_output.update(snapshot)
            self.screen_status_action.setText(self._screen_output.status_text)

    def toggle_screen_output(self, enabled: bool) -> None:
        self.config.screen.enabled = enabled
        self.config.save()
        if self._screen_output is not None:
            self._screen_output.close()
            self._screen_output = None
        if enabled:
            self._screen_output = ScreenOutput(CONFIG_DIR / 'gem12-status.png', self.config.screen.port)
            if self._last_snapshot is not None:
                self._screen_output.update(self._last_snapshot)
            self.screen_status_action.setText(self._screen_output.status_text)
        else:
            self.screen_status_action.setText('屏幕：未启用')

    def _shutdown_quota_worker(self) -> None:
        self.timer.stop()
        self._quota_executor.shutdown(wait=False, cancel_futures=True)
        if self._screen_output is not None:
            self._screen_output.close()

    def _apply_snapshot(self, snapshot: CodexSnapshot) -> None:
        color = STATUS_COLORS[snapshot.status]
        self.status_dot.setStyleSheet(f'background-color: {color}; border-radius: 6px;')
        self.title_label.setText(snapshot.status_text)
        self.primary_caption_label.setText(snapshot.primary_title)
        self.secondary_caption_label.setText(snapshot.secondary_title)
        for label in (
            self.primary_caption_label,
            self.primary_reset_label,
            self.primary_percentage_label,
        ):
            label.setVisible(snapshot.primary_visible)
        for label in (
            self.secondary_caption_label,
            self.secondary_reset_label,
            self.secondary_percentage_label,
        ):
            label.setVisible(snapshot.secondary_visible)
        primary_percentage, primary_reset = _quota_display_parts(
            snapshot.primary_text, snapshot.primary_title
        )
        secondary_percentage, secondary_reset = _quota_display_parts(
            snapshot.secondary_text, snapshot.secondary_title
        )
        self.primary_reset_label.setText(primary_reset)
        self.primary_percentage_label.setText(primary_percentage)
        self.secondary_reset_label.setText(secondary_reset)
        self.secondary_percentage_label.setText(secondary_percentage)
        self._note_display_text = _format_display_note(snapshot.note)
        self.note_label.setText(self._note_display_text)
        tooltip_note = snapshot.detail or snapshot.note
        tooltip_text = _format_panel_tooltip(snapshot, tooltip_note)
        _set_panel_tooltip(
            tooltip_text,
            self.card,
            self.status_dot,
            self.title_label,
            self.primary_caption_label,
            self.primary_reset_label,
            self.primary_percentage_label,
            self.secondary_caption_label,
            self.secondary_reset_label,
            self.secondary_percentage_label,
            self.note_label,
        )
        self.note_label.setVisible(bool(snapshot.note))
        self._resize_to_content()
        self._set_tray_icon(snapshot)
        self.tray.setToolTip(tooltip_text)

    def _resize_to_content(self) -> None:
        margins = self.card.layout().contentsMargins()
        spacing = self.card.layout().spacing()
        body_width = max(1, self.width() - margins.left() - margins.right())
        line_heights = [max(self.title_label.sizeHint().height(), self.status_dot.height())]

        if not self.primary_caption_label.isHidden():
            line_heights.append(
                max(
                    self.primary_caption_label.sizeHint().height(),
                    self.primary_reset_label.sizeHint().height(),
                    self.primary_percentage_label.sizeHint().height(),
                )
            )
        if not self.secondary_caption_label.isHidden():
            line_heights.append(
                max(
                    self.secondary_caption_label.sizeHint().height(),
                    self.secondary_reset_label.sizeHint().height(),
                    self.secondary_percentage_label.sizeHint().height(),
                )
            )

        if not self.note_label.isHidden():
            self.note_label.setFixedWidth(body_width)
            self.note_label.setText(_elide_text(self.note_label, self._note_display_text, body_width))
            line_heights.append(self.note_label.sizeHint().height())

        content_height = (
            margins.top()
            + margins.bottom()
            + sum(line_heights)
            + spacing * (len(line_heights) - 1)
            + 2
        )
        max_height = self._max_available_height()
        target_height = max(MIN_WIDGET_HEIGHT, min(content_height, max_height))
        if self.height() != target_height:
            self.setFixedHeight(target_height)
            self._keep_inside_screen()

    def mark_idle(self) -> None:
        self.reader.mark_idle()
        self.refresh()

    def toggle_lock(self) -> None:
        self.config.ui.locked = not self.config.ui.locked
        self.config.save()
        self.lock_action.setText(self._lock_text())

    def install_hook_to_codex(self) -> None:
        try:
            writer_path, hooks_path, backup_path = install_hooks()
        except Exception as exc:
            QMessageBox.critical(self, '添加钩子失败', str(exc))
            return

        message = f'已安装 hook writer：{writer_path}\n已更新 Codex hooks：{hooks_path}'
        if backup_path:
            message += f'\n已备份原 hooks.json：{backup_path}'
        message += '\n\n下一步：在 ChatGPT App 的 Codex 中打开 /hooks，review/trust 新 hook。'

        self.tray.showMessage(
            'Codex 状态',
            '已添加钩子到 Codex；请在 /hooks 中 trust 新 hook。',
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )
        QMessageBox.information(self, '已添加钩子到 Codex', message)
        self.refresh()

    def query_reset_credits(self) -> None:
        if self._reset_credits_reply is not None:
            return

        auth_path = self.config.codex.sessions_dir.parent / 'auth.json'
        try:
            access_token = read_access_token(auth_path)
        except ResetCreditsError as exc:
            QMessageBox.critical(self, '查询重置额度失败', str(exc))
            return

        request = QNetworkRequest(QUrl(RESET_CREDITS_URL))
        request.setRawHeader(b'Accept', b'application/json')
        request.setRawHeader(b'Authorization', f'Bearer {access_token}'.encode('utf-8'))
        request.setTransferTimeout(RESET_CREDITS_TIMEOUT_MS)

        self.reset_credits_action.setEnabled(False)
        reply = self._network_manager.get(request)
        self._reset_credits_reply = reply
        reply.finished.connect(lambda: self._finish_reset_credits_query(reply))

    def _finish_reset_credits_query(self, reply: QNetworkReply) -> None:
        if self._reset_credits_reply is reply:
            self._reset_credits_reply = None
        self.reset_credits_action.setEnabled(True)

        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        status_code = int(status) if status is not None else None
        try:
            if status_code == 401:
                raise ResetCreditsError(
                    'Codex 凭证已失效，或请求未被服务器识别为携带 Authorization。'
                    '请在 ChatGPT App 中重新登录后再试。'
                )
            if status_code is not None and status_code >= 400:
                raise ResetCreditsError(f'查询失败：服务器返回 HTTP {status_code}。')
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ResetCreditsError('查询失败：无法连接 ChatGPT，请检查网络后重试。')

            summary = parse_reset_credits_response(bytes(reply.readAll()))
            message = format_reset_credits(summary)
        except ResetCreditsError as exc:
            QMessageBox.critical(self, '查询重置额度失败', str(exc))
        else:
            QMessageBox.information(self, '重置额度', message)
        finally:
            reply.deleteLater()

    def open_sessions_dir(self) -> None:
        path = self.config.codex.sessions_dir
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_state_dir(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG_DIR)))

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        self.menu.exec(event.globalPos())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.config.ui.locked:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and not self.config.ui.locked:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self.config.ui.x = self.x()
            self.config.ui.y = self.y()
            self.config.save()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _restore_position(self) -> None:
        if self.config.ui.x is not None and self.config.ui.y is not None:
            self.move(self.config.ui.x, self.config.ui.y)
            self._keep_inside_screen()
            return

        screen = QApplication.primaryScreen()
        if screen is None:
            self.move(80, 80)
            return

        rect = screen.availableGeometry()
        x = rect.right() - self.width() - 24
        y = rect.top() + 64
        self.move(x, y)

    def _max_available_height(self) -> int:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return 600
        return max(MIN_WIDGET_HEIGHT, screen.availableGeometry().height() - 24)

    def _keep_inside_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        rect = screen.availableGeometry()
        x = min(max(self.x(), rect.left()), rect.right() - self.width() + 1)
        y = min(max(self.y(), rect.top()), rect.bottom() - self.height() + 1)
        if x != self.x() or y != self.y():
            self.move(x, y)

    def _lock_text(self) -> str:
        return '解锁位置' if self.config.ui.locked else '锁定位置'

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()
                self.activateWindow()

    def _set_tray_icon(self, snapshot: CodexSnapshot | None) -> None:
        status = snapshot.status if snapshot is not None else 'idle'
        number = _tray_quota_number(snapshot) if snapshot is not None else '--'
        key = (status, number)
        if key == self._tray_icon_key:
            return
        self.tray.setIcon(_make_tray_icon(STATUS_COLORS[status], number))
        self._tray_icon_key = key


def _tray_quota_number(snapshot: CodexSnapshot) -> str:
    for minutes in (300, 10080):
        for window in (snapshot.primary, snapshot.secondary):
            if window.window_minutes == minutes and window.remaining_percent is not None:
                return str(int(min(100, window.remaining_percent) + 0.5))
    return '--'


def _make_tray_icon(color: str, number: str) -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        stroke = max(2.0, size * 0.105)
        inset = stroke / 2 + 0.5
        painter.setPen(QPen(QColor(color), stroke))
        painter.setBrush(QColor('#20242c'))
        painter.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
        painter.setPen(QColor('white'))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(round(size * 0.54))
        while QFontMetrics(font).horizontalAdvance(number) > size * 0.72:
            font.setPixelSize(font.pixelSize() - 1)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, number)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _version_menu_text() -> str:
    return f'版本：{__version__}'


def _enable_inactive_tooltips(*widgets: QWidget) -> None:
    for widget in widgets:
        widget.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)


def _set_panel_tooltip(text: str, *widgets: QWidget) -> None:
    for widget in widgets:
        widget.setToolTip(text)


def _format_panel_tooltip(snapshot: CodexSnapshot, note: str) -> str:
    return '\n'.join(
        part
        for part in [
            snapshot.status_text,
            snapshot.primary_text if snapshot.primary_visible else '',
            snapshot.secondary_text if snapshot.secondary_visible else '',
            note,
        ]
        if part
    )


def _format_display_note(note: str) -> str:
    text = ' / '.join(part.strip() for part in note.splitlines() if part.strip())
    text = (
        text.replace('hook 工作中: ', '钩子：')
        .replace('hook 闲置: ', '钩子：')
        .replace('hook 工作状态已过期，视为闲置', '已自动恢复闲置')
        .replace('hook 活跃状态已过期，视为闲置', '已自动恢复闲置')
    )
    for event_name, label in sorted(HOOK_EVENT_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(event_name, label)
    return text


def _quota_display_parts(text: str, caption: str) -> tuple[str, str]:
    prefix = f'{caption}：'
    value = text[len(prefix) :].strip() if text.startswith(prefix) else text.strip()
    percentage, separator, reset_time = value.partition(' ')
    return percentage, reset_time.strip() if separator else ''


def _elide_text(label: QLabel, text: str, width: int) -> str:
    return QFontMetrics(label.font()).elidedText(text, Qt.TextElideMode.ElideRight, max(1, width))
