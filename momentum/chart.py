"""Custom-painted 5-minute candlestick/line chart with a volume pane (QPainter)."""
from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QFont, QFontMetrics
from PySide6.QtWidgets import QWidget

from .data import ET, fmt_price, fmt_vol, is_regular_bar

UP = QColor("#26C281")
DOWN = QColor("#EF5350")
GRID = QColor(255, 255, 255, 34)
AXIS = QColor("#B0B8C0")
EXT = QColor(255, 255, 255, 15)
PREV = QColor("#FFC857")
BG = QColor("#151B21")


def nice_step(raw: float) -> float:
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    f = raw / mag
    return (1 if f < 1.5 else 2 if f < 3 else 5 if f < 7 else 10) * mag


class ChartWidget(QWidget):
    """Wheel = zoom at cursor, drag = pan, hover = crosshair tooltip, double-click = reset."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(320)
        self.bars = []
        self.prev_close = float("nan")
        self.line_mode = False
        self.visible = -1.0   # bars on screen (-1 = all)
        self.right_off = 0.0  # bars hidden on the right
        self.hover = None
        self._drag_x = None
        self._key = None
        self.message = "Select a stock to see its 5-minute chart"

    # ---- data
    def set_data(self, bars, prev_close, key):
        if key != self._key:
            self.visible, self.right_off, self.hover = -1.0, 0.0, None
            self._key = key
        self.bars = bars
        self.prev_close = prev_close
        self.message = "No 5-minute bars for this range" if not bars else ""
        self.update()

    def clear(self, message):
        self.bars, self._key, self.message = [], None, message
        self.update()

    def set_line_mode(self, on):
        self.line_mode = on
        self.update()

    # ---- geometry helpers
    def _plot_w(self):
        return max(1.0, self.width() - 64)

    def _vis(self):
        n = len(self.bars)
        mn = min(12.0, max(n, 1))
        v = n if self.visible <= 0 else self.visible
        return min(max(v, mn), max(n, mn))

    def _clamp(self):
        self.right_off = min(max(self.right_off, 0.0), max(0.0, len(self.bars) - self._vis()))

    def _index_at(self, x):
        n = len(self.bars)
        if not n:
            return None
        v = self._vis()
        first = n - self.right_off - v
        return int(min(max(math.floor(first + x / (self._plot_w() / v)), 0), n - 1))

    # ---- input
    def wheelEvent(self, e):
        if not self.bars:
            return
        n = len(self.bars)
        v0 = self._vis(); bw0 = self._plot_w() / v0
        cx = min(max(e.position().x(), 0), self._plot_w())
        anchor = n - self.right_off - v0 + cx / bw0
        zoom = 1.15 ** (e.angleDelta().y() / 120)
        mn = min(12.0, n)
        v1 = min(max(v0 / zoom, mn), n)
        self.visible = v1
        self.right_off = n - (anchor - cx / (self._plot_w() / v1)) - v1
        self._clamp()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_x = e.position().x()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, e):
        self._drag_x = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, e):
        self.visible, self.right_off = -1.0, 0.0
        self.update()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag_x is not None and self.bars:
            dx = x - self._drag_x
            self._drag_x = x
            self.right_off += dx / (self._plot_w() / self._vis())
            self._clamp()
        self.hover = self._index_at(x) if 0 <= x <= self._plot_w() else None
        self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    # ---- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), BG)
        font = QFont(self.font()); font.setPointSizeF(8.5)
        p.setFont(font)
        fm = QFontMetrics(font)
        n = len(self.bars)
        if not n:
            p.setPen(AXIS)
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        axis_w, time_h, gap = 64, 20, 6
        pw = self._plot_w()
        total_h = self.height() - time_h
        price_h = total_h * 0.74
        vol_top = price_h + gap
        vol_h = total_h - vol_top
        self._clamp()
        v = self._vis(); bw = pw / v
        first = n - self.right_off - v
        i0 = max(0, math.floor(first)); i1 = min(n - 1, math.ceil(first + v))
        xo = lambda i: (i - first) * bw + bw / 2

        lo = min(b.l for b in self.bars[i0:i1 + 1]); hi = max(b.h for b in self.bars[i0:i1 + 1])
        vmax = max(1, max(b.v for b in self.bars[i0:i1 + 1]))
        pc = self.prev_close
        if pc == pc and pc > 0 and (lo - (hi - lo)) <= pc <= (hi + (hi - lo)):
            lo, hi = min(lo, pc), max(hi, pc)
        if hi - lo < 1e-9:
            hi += 0.01; lo -= 0.01
        pad = (hi - lo) * 0.06; lo -= pad; hi += pad
        yo = lambda price: (hi - price) / (hi - lo) * price_h

        p.save(); p.setClipRect(QRectF(0, 0, pw, total_h))
        for i in range(i0, i1 + 1):
            if not is_regular_bar(self.bars[i].t):
                p.fillRect(QRectF(xo(i) - bw / 2, 0, bw, total_h), EXT)
        p.restore()

        step = nice_step((hi - lo) / 5)
        g = math.ceil(lo / step) * step
        while g <= hi:
            y = yo(g)
            p.setPen(QPen(GRID, 1)); p.drawLine(QPointF(0, y), QPointF(pw, y))
            p.setPen(AXIS); p.drawText(QPointF(pw + 6, y + 4), fmt_price(g))
            g += step
        p.setPen(QPen(GRID, 1)); p.drawLine(QPointF(0, vol_top - gap / 2), QPointF(pw, vol_top - gap / 2))

        # time labels (ET)
        every = max(1, math.ceil(90 / bw)); last = -10**9
        for i in range(i0, i1 + 1):
            z = datetime.fromtimestamp(self.bars[i].t, ET)
            new_day = i > 0 and datetime.fromtimestamp(self.bars[i - 1].t, ET).date() != z.date()
            due = i % every == 0 and i - last >= every
            if not (new_day or due) or (new_day and i - last < every / 2):
                continue
            x = xo(i)
            txt = z.strftime("%a %m/%d") if new_day else z.strftime("%I:%M").lstrip("0")
            tw = fm.horizontalAdvance(txt)
            if x < 0 or x > pw - 8:
                continue
            last = i
            tx = min(max(2.0, x - tw / 2), pw - tw - 2)   # keep the whole label inside the plot
            p.setPen(QPen(GRID, 1)); p.drawLine(QPointF(x, 0), QPointF(x, total_h))
            p.setPen(AXIS); p.drawText(QPointF(tx, total_h + 14), txt)

        p.save(); p.setClipRect(QRectF(0, 0, pw, total_h))
        for i in range(i0, i1 + 1):  # volume
            b = self.bars[i]; h = b.v / vmax * vol_h
            c = QColor(UP if b.c >= b.o else DOWN); c.setAlpha(140)
            p.fillRect(QRectF(xo(i) - bw * 0.4, vol_top + vol_h - h, bw * 0.8, h), c)
        if self.line_mode:
            path = QPainterPath()
            for i in range(i0, i1 + 1):
                pt = QPointF(xo(i), yo(self.bars[i].c))
                path.moveTo(pt) if i == i0 else path.lineTo(pt)
            ref = pc if pc == pc and pc > 0 else self.bars[i0].o
            p.setPen(QPen(UP if self.bars[i1].c >= ref else DOWN, 2)); p.setBrush(Qt.NoBrush); p.drawPath(path)
        else:
            body = max(1.0, bw * 0.7)
            for i in range(i0, i1 + 1):
                b = self.bars[i]; x = xo(i); c = UP if b.c >= b.o else DOWN
                p.setPen(QPen(c, max(1.0, bw * 0.12))); p.drawLine(QPointF(x, yo(b.h)), QPointF(x, yo(b.l)))
                top, bot = yo(max(b.o, b.c)), yo(min(b.o, b.c))
                p.fillRect(QRectF(x - body / 2, top, body, max(1.0, bot - top)), c)
        if pc == pc and lo <= pc <= hi:
            pen = QPen(PREV, 1.5); pen.setDashPattern([6, 4]); p.setPen(pen)
            p.drawLine(QPointF(0, yo(pc)), QPointF(pw, yo(pc)))
        p.restore()
        if pc == pc and lo <= pc <= hi:
            f2 = QFont(font); f2.setPointSizeF(7.5); p.setFont(f2); p.setPen(PREV)
            p.drawText(QPointF(pw + 4, yo(pc) + 13), f"Prev {fmt_price(pc)}"); p.setFont(font)

        # crosshair + tooltip
        if self.hover is not None and i0 <= self.hover <= i1:
            b = self.bars[self.hover]; x = xo(self.hover); y = yo(b.c)
            p.setPen(QPen(QColor(255, 255, 255, 150), 1)); p.drawLine(QPointF(x, 0), QPointF(x, total_h))
            p.setPen(QPen(QColor(255, 255, 255, 100), 1)); p.drawLine(QPointF(0, y), QPointF(pw, y))
            p.setBrush(Qt.white); p.drawEllipse(QPointF(x, y), 3, 3)
            lines = [datetime.fromtimestamp(b.t, ET).strftime("%a %m/%d %I:%M %p ET"),
                     f"O {fmt_price(b.o)}   H {fmt_price(b.h)}",
                     f"L {fmt_price(b.l)}   C {fmt_price(b.c)}",
                     f"Vol {fmt_vol(b.v)}"]
            w = max(fm.horizontalAdvance(s) for s in lines) + 20; h = fm.height() * len(lines) + 14
            left = x + 12 if x + 12 + w < pw else max(0, x - 12 - w)
            p.setPen(Qt.NoPen); p.setBrush(QColor(32, 40, 48, 230))
            p.drawRoundedRect(QRectF(left, 6, w, h), 8, 8)
            p.setPen(Qt.white)
            for k, s in enumerate(lines):
                p.drawText(QPointF(left + 10, 6 + 7 + fm.ascent() + k * fm.height()), s)
        p.end()
