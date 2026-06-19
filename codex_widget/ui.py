from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .config import CONFIG_DIR, AppConfig
from .models import CodexSnapshot, StatusName
from .snapshot import CodexSnapshotReader


STATUS_COLORS: dict[StatusName, str] = {
    'idle': '#31c46b',
    'working': '#f4c542',
    'cooldown': '#ff5a5f',
}

MIN_WIDGET_HEIGHT = 86


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
        )
        self._drag_offset: QPoint | None = None
        self._last_snapshot: CodexSnapshot | None = None

        self._build_window()
        self._build_ui()
        self._build_menu()
        self._build_tray()
        self._restore_position()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(self.config.status.refresh_interval_seconds * 1000)
        self.refresh()

    def _build_window(self) -> None:
        self.setWindowTitle('Codex 状态')
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
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

        self.primary_label = QLabel('5小时：未读取', self.card)
        self.secondary_label = QLabel('周额度：未读取', self.card)
        self.note_label = QLabel('', self.card)
        self.note_label.setObjectName('noteLabel')
        self.note_label.setWordWrap(True)

        layout.addWidget(self.primary_label)
        layout.addWidget(self.secondary_label)
        layout.addWidget(self.note_label)

    def _build_menu(self) -> None:
        self.menu = QMenu(self)

        self.refresh_action = QAction('刷新', self)
        self.refresh_action.triggered.connect(self.refresh)
        self.menu.addAction(self.refresh_action)

        self.mark_idle_action = QAction('标记为闲置', self)
        self.mark_idle_action.triggered.connect(self.mark_idle)
        self.menu.addAction(self.mark_idle_action)

        self.lock_action = QAction(self._lock_text(), self)
        self.lock_action.triggered.connect(self.toggle_lock)
        self.menu.addAction(self.lock_action)

        self.open_sessions_action = QAction('打开 sessions 目录', self)
        self.open_sessions_action.triggered.connect(self.open_sessions_dir)
        self.menu.addAction(self.open_sessions_action)

        self.open_state_action = QAction('打开状态目录', self)
        self.open_state_action.triggered.connect(self.open_state_dir)
        self.menu.addAction(self.open_state_action)

        self.menu.addSeparator()

        self.quit_action = QAction('退出', self)
        self.quit_action.triggered.connect(QApplication.quit)
        self.menu.addAction(self.quit_action)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip('Codex 状态')
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._set_tray_icon('idle')
        self.tray.show()

    def refresh(self) -> None:
        snapshot = self.reader.read_snapshot()
        self._last_snapshot = snapshot
        self._apply_snapshot(snapshot)

    def _apply_snapshot(self, snapshot: CodexSnapshot) -> None:
        color = STATUS_COLORS[snapshot.status]
        self.status_dot.setStyleSheet(f'background-color: {color}; border-radius: 6px;')
        self.title_label.setText(snapshot.status_text)
        self.primary_label.setText(snapshot.primary_text)
        self.secondary_label.setText(snapshot.secondary_text)
        self.note_label.setText(snapshot.note)
        self.note_label.setVisible(bool(snapshot.note))
        self._resize_to_content()
        self._set_tray_icon(snapshot.status)
        self.tray.setToolTip(
            '\n'.join(
                part
                for part in [
                    snapshot.status_text,
                    snapshot.primary_text,
                    snapshot.secondary_text,
                    snapshot.note,
                ]
                if part
            )
        )

    def _resize_to_content(self) -> None:
        margins = self.card.layout().contentsMargins()
        spacing = self.card.layout().spacing()
        body_width = max(1, self.width() - margins.left() - margins.right())
        note_height = 0
        line_count = 3

        if self.note_label.isVisible():
            self.note_label.setFixedWidth(body_width)
            note_height = self.note_label.heightForWidth(body_width)
            if note_height < 0:
                note_height = self.note_label.sizeHint().height()
            line_count += 1

        content_height = (
            margins.top()
            + margins.bottom()
            + max(self.title_label.sizeHint().height(), self.status_dot.height())
            + self.primary_label.sizeHint().height()
            + self.secondary_label.sizeHint().height()
            + note_height
            + spacing * (line_count - 1)
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

    def _set_tray_icon(self, status: StatusName) -> None:
        self.tray.setIcon(_make_dot_icon(STATUS_COLORS[status]))


def _make_dot_icon(color: str) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(QColor(255, 255, 255, 170))
    painter.drawEllipse(5, 5, 22, 22)
    painter.end()
    return QIcon(pixmap)
