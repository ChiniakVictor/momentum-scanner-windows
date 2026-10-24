"""Cartoon Kodiak bear shaking a salmon, painted with QPainter. ~3.5 s, click to dismiss."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QPointF, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QLinearGradient, QFont, QBrush
from PySide6.QtWidgets import QWidget

FUR = QColor("#8B5A2B"); FUR_DARK = QColor("#5E3A1A"); FUR_LIGHT = QColor("#C89B6D"); NOSE = QColor("#2B1B10")
S_BACK = QColor("#6E8FA3"); S_BELLY = QColor("#F4C7B0"); S_STRIPE = QColor("#E2574C")

DURATION = 3.8


class BearOverlay(QWidget):
    """Child overlay anchored to the bottom-right of its parent window."""
    finished = Signal()

    W, H = 230, 270

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(self.W, self.H)
        self.hide()
        self.symbols = []
        self.t0 = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def play(self, symbols):
        self.symbols = list(symbols)[:3]
        self.t0 = time.monotonic()
        self._place(0)
        self.show(); self.raise_()
        self.timer.start(16)

    def _place(self, slide):
        par = self.parentWidget()
        x = par.width() - self.W - 16 + int(slide * 260)
        y = par.height() - self.H - 40 + int(slide * 120)
        self.move(x, y)

    def _tick(self):
        t = time.monotonic() - self.t0
        if t >= DURATION:
            self.stop(); return
        enter = 1 - math.exp(-t * 7) * math.cos(t * 9)          # springy slide-in
        out = max(0.0, (t - (DURATION - 0.5)) / 0.5)              # fade/slide out at the end
        self._place((1 - min(enter, 1.08)) + out * 0.5)
        self.opacity = 1 - out
        self.update()

    def stop(self):
        self.timer.stop(); self.hide(); self.finished.emit()

    def mousePressEvent(self, e):
        self.stop()

    def paintEvent(self, _):
        t = time.monotonic() - self.t0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(getattr(self, "opacity", 1.0))
        # speech bubble
        f = QFont(self.font()); f.setBold(True); f.setPointSizeF(11); p.setFont(f)
        txt = "New catch! " + " ".join(self.symbols)
        w = min(self.W - 4, p.fontMetrics().horizontalAdvance(txt) + 24)
        p.setPen(Qt.NoPen); p.setBrush(QColor(255, 244, 224, 240))
        p.drawRoundedRect(QRectF(self.W - w - 2, 2, w, 30), 12, 12)
        p.setPen(QColor("#3B2412")); p.drawText(QRectF(self.W - w - 2, 2, w, 30), Qt.AlignCenter, txt)
        p.translate(15, 40)
        p.scale(1.0, 1.0)
        draw_bear(p, t)
        p.end()


def _ellipse(p, color, x, y, w, h):
    p.setPen(Qt.NoPen); p.setBrush(color); p.drawEllipse(QRectF(x, y, w, h))


def draw_bear(p: QPainter, t: float):
    # designed on a 200x200 grid
    shake = math.sin(t * 2 * math.pi * 3.2)
    _ellipse(p, FUR_DARK, 30, 140, 140, 90)
    _ellipse(p, FUR, 36, 146, 128, 80)
    p.save()
    p.translate(100, 175); p.rotate(shake * 14); p.translate(-100, -175)
    for ex in (52, 148):
        _ellipse(p, FUR_DARK, ex - 20, 32, 40, 40)
        _ellipse(p, FUR_LIGHT, ex - 10, 44, 20, 20)
    _ellipse(p, FUR_DARK, 38, 44, 124, 116)
    _ellipse(p, FUR, 42, 46, 116, 110)
    for side in (-1, 1):
        path = QPainterPath(QPointF(100 + side * 56, 112))
        for (dx, y) in ((66, 120), (55, 124), (63, 132), (50, 134)):
            path.lineTo(100 + side * dx, y)
        path.closeSubpath(); p.setBrush(FUR); p.drawPath(path)
    pen = QPen(NOSE, 4); pen.setCapStyle(Qt.RoundCap); p.setPen(pen); p.setBrush(Qt.NoBrush)
    for ex in (78, 122):  # happy squinting eyes
        p.drawArc(QRectF(ex - 10, 84, 20, 14), 20 * 16, 140 * 16)
    _ellipse(p, FUR_LIGHT, 66, 104, 68, 50)
    _ellipse(p, NOSE, 88, 106, 24, 15)
    _ellipse(p, QColor(255, 255, 255, 128), 93, 108, 7, 4)
    _ellipse(p, QColor("#4A1F1A"), 78, 128, 44, 22)
    # salmon
    p.save()
    fish_angle = math.sin(t * 2 * math.pi * 3.2 - 0.6) * 10
    p.translate(100, 138); p.rotate(fish_angle); p.translate(-100, -138)
    draw_salmon(p, math.sin(t * 2 * math.pi * 6.4))
    p.restore()
    p.setPen(Qt.NoPen); p.setBrush(Qt.white)
    for tx in (86, 96, 104, 114):
        tooth = QPainterPath(QPointF(tx - 4, 129)); tooth.lineTo(tx + 4, 129); tooth.lineTo(tx, 137); tooth.closeSubpath()
        p.drawPath(tooth)
    p.setBrush(FUR_LIGHT); p.drawChord(QRectF(80, 136, 40, 18), 180 * 16, 180 * 16)
    p.restore()
    # droplets & scales
    for k in range(14):
        born = (k % 7) * 0.45 + (k // 7) * 0.2
        age = t - born
        if age < 0 or age > 0.9:
            continue
        d = -1 if k % 2 == 0 else 1
        x = 100 + d * (60 + (k % 3) * 6) + d * (55 + (k * 13 % 40)) * age
        y = 135 - (70 + (k * 17 % 50)) * age + 160 * age * age
        a = int(255 * max(0.0, 1 - age / 0.9))
        if k % 4 == 3:
            _ellipse(p, QColor(221, 231, 238, a), x - 3, y - 2, 6, 4)
        else:
            r = 3 + k % 3
            drop = QPainterPath(QPointF(x, y - r * 2))
            drop.cubicTo(x + r * 1.2, y - r * 0.4, x + r, y + r, x, y + r)
            drop.cubicTo(x - r, y + r, x - r * 1.2, y - r * 0.4, x, y - r * 2)
            p.setPen(Qt.NoPen); p.setBrush(QColor(126, 200, 245, a)); p.drawPath(drop)


def draw_salmon(p: QPainter, flop: float):
    cy, left, right = 138, 22, 160
    body = QPainterPath(QPointF(left, cy))
    body.cubicTo(left + 20, cy - 22, right - 40, cy - 20, right, cy - 3)
    body.lineTo(right, cy + 3)
    body.cubicTo(right - 40, cy + 18, left + 20, cy + 20, left, cy)
    grad = QLinearGradient(0, cy - 18, 0, cy + 16); grad.setColorAt(0, S_BACK); grad.setColorAt(1, S_BELLY)
    p.setPen(Qt.NoPen); p.setBrush(QBrush(grad)); p.drawPath(body)
    stripe = QPainterPath(QPointF(left + 18, cy + 1))
    stripe.cubicTo(left + 50, cy - 8, right - 40, cy - 6, right - 6, cy - 1)
    stripe.cubicTo(right - 40, cy + 6, left + 50, cy + 8, left + 18, cy + 1)
    c = QColor(S_STRIPE); c.setAlpha(217); p.setBrush(c); p.drawPath(stripe)
    p.setBrush(QColor("#2E3B45"))
    for sx, sy in ((60, -10), (78, -12), (98, -11), (118, -9), (135, -7), (70, -6), (108, -5)):
        p.drawEllipse(QPointF(sx, cy + sy), 1.6, 1.6)
    p.save(); p.translate(right, cy); p.rotate(flop * 22)
    tail = QPainterPath(QPointF(-2, 0)); tail.lineTo(22, -16); tail.lineTo(16, 0); tail.lineTo(22, 16); tail.closeSubpath()
    p.setBrush(S_BACK); p.drawPath(tail); p.restore()
    dorsal = QPainterPath(QPointF(88, cy - 16)); dorsal.lineTo(100, cy - 27); dorsal.lineTo(108, cy - 15); dorsal.closeSubpath()
    p.drawPath(dorsal)
    p.setPen(QPen(QColor("#4F6675"), 1.6)); p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(left + 10, cy - 10, 14, 20), -60 * 16, 120 * 16)
    p.setPen(Qt.NoPen); p.setBrush(Qt.white); p.drawEllipse(QPointF(left + 9, cy - 4), 4, 4)
    p.setBrush(Qt.black); p.drawEllipse(QPointF(left + 8.5, cy - 4), 2.2, 2.2)
    p.setPen(QPen(QColor("#4F6675"), 1.5)); p.drawLine(QPointF(left, cy + 1), QPointF(left + 6, cy + 3))
