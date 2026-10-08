from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QRadialGradient

from .models import CodexSnapshot, StatusName


SCREEN_WIDTH = 960
SCREEN_HEIGHT = 376
STATUS_COLORS: dict[StatusName, str] = {
    'idle': '#31c46b',
    'working': '#4aa3ff',
    'waiting': '#ff9f1c',
    'cooldown': '#ff5a5f',
    'offline': '#ff5a5f',
}
BACKGROUND = '#080f19'
FOREGROUND = '#f1f6fc'
MUTED = '#a9bacd'
ACCENT = '#49d5ed'


@dataclass(frozen=True, slots=True)
class ScreenQuota:
    title: str
    remaining: int | None
    reset: str


@dataclass(frozen=True, slots=True)
class ScreenFrame:
    status: StatusName
    status_text: str
    summary: str
    quotas: tuple[ScreenQuota, ...]


def _reset_text(value: datetime | None, minutes: int | None) -> str:
    if value is None:
        return '重置时间未知'
    local = value.astimezone()
    prefix = '' if minutes == 300 else f'{local.month}月{local.day}日 '
    return f'{prefix}{local:%H:%M} 重置'


def frame_from_snapshot(snapshot: CodexSnapshot) -> ScreenFrame:
    windows = [
        (title, window)
        for title, window, visible in (
            (snapshot.primary_title, snapshot.primary, snapshot.primary_visible),
            (snapshot.secondary_title, snapshot.secondary, snapshot.secondary_visible),
        )
        if visible and any(value is not None for value in (
            window.window_minutes, window.used_percent, window.resets_at,
        ))
    ]
    windows.sort(key=lambda pair: {300: 0, 10080: 1}.get(pair[1].window_minutes, 2))
    quotas = []
    for title, window in windows:
        title = {300: '5 小时', 10080: '周额度'}.get(window.window_minutes, title or '额度')
        remaining = window.remaining_percent
        percent = None
        if remaining is not None and math.isfinite(remaining):
            percent = int(max(0, min(100, remaining)) + 0.5)
        quotas.append(ScreenQuota(f'{title}剩余', percent, _reset_text(window.resets_at, window.window_minutes)))
    if not quotas:
        quotas.append(ScreenQuota('额度未读取', None, '重置时间未知'))

    hook = snapshot.hook_signal
    summary = f'工作 {hook.working_count} · 待确认 {hook.waiting_count}'
    if snapshot.status == 'offline':
        summary = '桌面应用未运行'
    elif snapshot.status == 'cooldown':
        summary = '额度受限，等待恢复'
    return ScreenFrame(snapshot.status, snapshot.status_text, summary, tuple(quotas))


def _text(painter: QPainter, rect: QRectF, text: str, size: int, color: str = FOREGROUND,
          *, center: bool = True, medium: bool = False) -> None:
    font = QFont('Microsoft YaHei UI')
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.Medium if medium else QFont.Weight.Normal)
    painter.setFont(font)
    painter.setPen(QColor(color))
    alignment = Qt.AlignmentFlag.AlignVCenter
    alignment |= Qt.AlignmentFlag.AlignHCenter if center else Qt.AlignmentFlag.AlignLeft
    painter.drawText(rect, alignment, text)


def render_screen(frame: ScreenFrame) -> QImage:
    """在固定像素画布上绘制；与桌面缩放比例及挂件大小无关。"""
    image = QImage(SCREEN_WIDTH, SCREEN_HEIGHT, QImage.Format.Format_RGB32)
    image.setDevicePixelRatio(1)
    image.fill(QColor(BACKGROUND))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        _text(painter, QRectF(32, 18, 112, 34), 'CODEX', 22, medium=True, center=False)
        _text(painter, QRectF(145, 18, 240, 34), '/ 状态与额度', 20, MUTED, center=False)

        two_columns = len(frame.quotas) == 1
        centers = (250, 710) if two_columns else (177, 492, 792)
        divider = 480 if two_columns else 326
        painter.setPen(QPen(QColor('#243448'), 1))
        painter.drawLine(divider, 85, divider, 338)
        cx = centers[0]
        _text(painter, QRectF(cx - 140, 71, 280, 36), '运行状态', 25, MUTED)

        status_color = QColor(STATUS_COLORS[frame.status])
        glow = QRadialGradient(cx, 216, 138)
        glow_color = QColor(status_color)
        glow_color.setAlpha(28)
        glow.setColorAt(0, glow_color)
        glow_color.setAlpha(0)
        glow.setColorAt(1, glow_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(cx - 138, 116, 276, 200))

        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(66 if two_columns else 54)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(frame.status_text)
        dot_size, gap = 16, 16
        left = cx - (width + dot_size + gap) / 2
        painter.setBrush(status_color)
        painter.drawEllipse(QRectF(left, 208, dot_size, dot_size))
        _text(painter, QRectF(left + dot_size + gap, 167, width + 2, 98),
              frame.status_text, font.pixelSize(), medium=True)
        _text(painter, QRectF(cx - 150, 312, 300, 36), frame.summary, 21, MUTED)

        for cx, quota in zip(centers[1:], frame.quotas):
            _text(painter, QRectF(cx - 140, 71, 280, 36), quota.title, 25, MUTED)
            ring = QRectF(cx - 91, 120, 182, 182)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(QColor('#1c2b3c'), 12)
            painter.setPen(pen)
            painter.drawEllipse(ring)
            if quota.remaining:
                pen.setColor(QColor(ACCENT))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawArc(ring, 90 * 16, -round(360 * 16 * quota.remaining / 100))
            number = '--' if quota.remaining is None else str(quota.remaining)
            size = 70 if two_columns else 62
            number_font = QFont('Microsoft YaHei UI')
            number_font.setPixelSize(size)
            number_font.setWeight(QFont.Weight.Medium)
            number_width = QFontMetricsF(number_font).horizontalAdvance(number)
            percent_font = QFont('Microsoft YaHei UI')
            percent_font.setPixelSize(25)
            percent_width = QFontMetricsF(percent_font).horizontalAdvance('%')
            gap = 6
            group_width = number_width
            if quota.remaining is not None:
                group_width += gap + percent_width
            left = cx - group_width / 2
            _text(painter, QRectF(left, 159, number_width, 96), number, size, medium=True)
            if quota.remaining is not None:
                _text(painter, QRectF(left + number_width + gap, 199, percent_width, 36), '%', 25, MUTED)
            _text(painter, QRectF(cx - 148, 312, 296, 36), quota.reset, 21, MUTED)
    finally:
        painter.end()
    return image
