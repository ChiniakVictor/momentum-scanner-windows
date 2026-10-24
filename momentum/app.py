"""Momentum Scanner for Windows: main window."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from html import escape

from PySide6.QtCore import Qt, QObject, QTimer, Signal, QUrl
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QColor, QPalette, QIcon, QPixmap, QPainter, QPen, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QTextBrowser, QDialog, QFormLayout,
    QDoubleSpinBox, QSpinBox, QCheckBox, QDialogButtonBox, QAbstractItemView, QButtonGroup, QFrame,
    QSystemTrayIcon, QMenu,
)

from . import data as D
from . import toast
from .bear import BearOverlay
from .chart import ChartWidget

GREEN = "#2EE59D"; AMBER = "#FFC857"; BG = "#101418"; CARD = "#171D23"; TEXT = "#E6EAEE"; SUB = "#9AA4AE"
DISCLAIMER = ("Informational only, not financial advice. Data from Yahoo Finance may be delayed ~15 min or "
              "incomplete; news detection is automated and imperfect.")


def app_icon() -> QIcon:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    pm = QPixmap(256, 256); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#0E2A1E")); p.setPen(Qt.NoPen); p.drawRoundedRect(8, 8, 240, 240, 48, 48)
    pen = QPen(QColor(GREEN), 22); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(52, 182), QPointF(104, 128), QPointF(140, 156), QPointF(196, 88)]))
    p.setPen(Qt.NoPen); p.setBrush(QColor(GREEN))
    p.drawPolygon(QPolygonF([QPointF(160, 70), QPointF(214, 70), QPointF(214, 124)]))
    p.end()
    return QIcon(pm)


class Bus(QObject):
    scan_done = Signal(object, bool)      # ScanResult, initial
    scan_failed = Signal(str)
    chart_done = Signal(object, object, str)   # ChartData, news list | None, key
    chart_failed = Signal(str, str)


class SettingsDialog(QDialog):
    def __init__(self, st: D.Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        f = QFormLayout(self)
        def dspin(v, lo, hi, step, dec=2):
            w = QDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(dec); w.setSingleStep(step); w.setValue(v); return w
        def ispin(v, lo, hi, step):
            w = QSpinBox(); w.setRange(lo, hi); w.setSingleStep(step); w.setValue(v); w.setGroupSeparatorShown(True); return w
        self.pct = dspin(st.min_pct_change, 0, 1000, 5, 1)
        self.price = dspin(st.max_price, 0.0001, 10000, 0.5)
        self.rvol = dspin(st.min_rel_vol, 0, 1000, 0.5, 1)
        self.vol = ispin(st.min_volume, 0, 2_000_000_000, 100_000)
        self.hours = ispin(st.news_lookback_hours, 1, 168, 1)
        self.refresh = ispin(st.auto_refresh_seconds, 30, 600, 10)
        self.timeadj = QCheckBox("Compare volume with what's normal by this time of day"); self.timeadj.setChecked(st.time_adjusted_rel_vol)
        self.ext = QCheckBox("Auto-refresh in pre-market (4:00–9:30) and after hours (16:00–20:00) ET"); self.ext.setChecked(st.extended_hours)
        self.otc = QCheckBox("Include OTC / pink sheets (off = NYSE, Nasdaq, NYSE American, Arca, Cboe)"); self.otc.setChecked(st.include_otc)
        self.toast = QCheckBox("Windows notification when a NEW stock meets all four (once per stock per day)"); self.toast.setChecked(st.toast_alerts)
        self.bear = QCheckBox("Bear celebration when a new stock meets all four"); self.bear.setChecked(st.bear_celebration)
        self.only_open = QCheckBox("Scan only when app is open")
        self.only_open.setToolTip("Off = closing the window hides it to the system tray and it keeps scanning during market hours")
        self.only_open.setChecked(st.scan_only_when_open)
        f.addRow("Minimum % change today", self.pct)
        f.addRow("Maximum price ($)", self.price)
        f.addRow("Minimum relative volume (x)", self.rvol)
        f.addRow("Minimum volume (shares)", self.vol)
        f.addRow("News lookback (hours)", self.hours)
        f.addRow("Auto-refresh (seconds)", self.refresh)
        for w in (self.timeadj, self.ext, self.otc, self.toast, self.bear, self.only_open):
            f.addRow(w)
        note = QLabel("Scan only when app is open: ON = closing the window exits. OFF = closing hides it to the\n"
                      "system tray (Open / Scan now / Quit) and it keeps scanning during market hours."); note.setStyleSheet(f"color:{SUB}")
        f.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); f.addRow(bb)

    def result_settings(self) -> D.Settings:
        return D.Settings(
            min_pct_change=self.pct.value(), max_price=self.price.value(), min_rel_vol=self.rvol.value(),
            min_volume=self.vol.value(), news_lookback_hours=self.hours.value(),
            include_otc=self.otc.isChecked(), time_adjusted_rel_vol=self.timeadj.isChecked(),
            extended_hours=self.ext.isChecked(), auto_refresh_seconds=self.refresh.value(),
            toast_alerts=self.toast.isChecked(), bear_celebration=self.bear.isChecked(),
            scan_only_when_open=self.only_open.isChecked())


class StockTable(QTableWidget):
    COLS = ["Symbol", "Name", "Price", "% Chg", "Volume", "Rel vol", "Catalyst / latest headline"]

    def __init__(self):
        super().__init__(0, len(self.COLS))
        self.setHorizontalHeaderLabels(self.COLS)
        self.verticalHeader().hide()
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False); self.setAlternatingRowColors(True); self.setWordWrap(False)
        h = self.horizontalHeader()
        for i in range(6):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(6, QHeaderView.Stretch)
        self.verticalHeader().setDefaultSectionSize(30)

    def fill(self, hits, lookback, selected):
        self.blockSignals(True)
        self.setRowCount(len(hits))
        for r, h in enumerate(hits):
            n = h.catalyst
            head = (f"📰 {n.title}  ·  {n.publisher}, {D.ago(n.published)}" if n else
                    f"No catalyst in last {lookback}h" + (f"  ·  latest: {h.latest_news.title} ({D.ago(h.latest_news.published)})" if h.latest_news else ""))
            vals = [h.symbol, h.name, "$" + D.fmt_price(h.price), f"+{h.pct_change:.1f}%", D.fmt_vol(h.volume), f"{h.rel_vol:.1f}x", head]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, h.symbol)
                if c == 0:
                    f = it.font(); f.setBold(True); it.setFont(f)
                if c == 3:
                    it.setForeground(QColor(GREEN))
                if c == 6:
                    it.setForeground(QColor(GREEN if n else AMBER)); it.setToolTip(v)
                if c in (2, 3, 4, 5):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.setItem(r, c, it)
            if h.symbol == selected:
                self.selectRow(r)
        self.blockSignals(False)


class MainWindow(QMainWindow):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.st = D.Settings.load()
        self.client = D.YahooClient()
        self.bus = Bus()
        self.result: D.ScanResult | None = None
        self.prev_matches: set | None = None
        self.selected: str | None = None
        self.range = "1d"
        self.chart_data: D.ChartData | None = None
        self.news: list = []
        self.scanning = False
        self.chart_loading = False

        self.setWindowTitle("Momentum Scanner")
        self.setWindowIcon(app_icon())
        self.resize(1500, 900)

        root = QWidget(); self.setCentralWidget(root)
        v = QVBoxLayout(root); v.setContentsMargins(12, 10, 12, 6); v.setSpacing(6)

        # header
        hdr = QHBoxLayout()
        title = QLabel("Momentum Scanner"); title.setStyleSheet("font-size:20px;font-weight:700")
        self.status = QLabel(); self.status.setStyleSheet(f"color:{SUB}")
        b_ref = QPushButton("⟳  Refresh"); b_ref.clicked.connect(lambda: self.refresh_scan())
        b_set = QPushButton("⚙  Settings"); b_set.clicked.connect(self.open_settings)
        hdr.addWidget(title); hdr.addSpacing(14); hdr.addWidget(self.status, 1); hdr.addWidget(b_ref); hdr.addWidget(b_set)
        v.addLayout(hdr)

        split = QSplitter(Qt.Horizontal); v.addWidget(split, 1)
        # left: tabs
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.t_match = StockTable(); self.t_near = StockTable()
        self.tabs.addTab(self.t_match, "All 4 criteria (0)"); self.tabs.addTab(self.t_near, "Near misses (0)")
        for t in (self.t_match, self.t_near):
            t.itemSelectionChanged.connect(lambda t=t: self._table_selected(t))
            t.cellClicked.connect(lambda r, c, t=t: self._table_clicked(t, r))
        self.err = QLabel(); self.err.setStyleSheet("color:#EF5350"); self.err.hide()
        lv.addWidget(self.err); lv.addWidget(self.tabs)
        split.addWidget(left)

        # right: detail
        right = QFrame(); right.setObjectName("detail")
        dv = QVBoxLayout(right); dv.setContentsMargins(14, 10, 14, 10); dv.setSpacing(8)
        top = QHBoxLayout()
        self.d_sym = QLabel("—"); self.d_sym.setStyleSheet("font-size:22px;font-weight:700")
        self.d_name = QLabel(""); self.d_name.setStyleSheet(f"color:{SUB}")
        self.b_yahoo = QPushButton("View on Yahoo"); self.b_yahoo.clicked.connect(self.open_yahoo)
        self.b_dref = QPushButton("⟳"); self.b_dref.setToolTip("Refresh chart"); self.b_dref.clicked.connect(lambda: self.refresh_chart(True))
        top.addWidget(self.d_sym); top.addSpacing(8); top.addWidget(self.d_name, 1); top.addWidget(self.b_yahoo); top.addWidget(self.b_dref)
        dv.addLayout(top)
        self.d_stats = QLabel(""); self.d_stats.setTextFormat(Qt.RichText)
        dv.addWidget(self.d_stats)
        chips = QHBoxLayout()
        self.b_1d = QPushButton("1D · 5m"); self.b_5d = QPushButton("5D · 5m")
        self.b_candle = QPushButton("Candles"); self.b_line = QPushButton("Line")
        for b in (self.b_1d, self.b_5d, self.b_candle, self.b_line):
            b.setCheckable(True); b.setObjectName("chip")
        g1 = QButtonGroup(self); g1.addButton(self.b_1d); g1.addButton(self.b_5d)
        g2 = QButtonGroup(self); g2.addButton(self.b_candle); g2.addButton(self.b_line)
        self.b_1d.setChecked(True); self.b_candle.setChecked(True)
        self.b_1d.clicked.connect(lambda: self.set_range("1d")); self.b_5d.clicked.connect(lambda: self.set_range("5d"))
        self.b_candle.clicked.connect(lambda: self.chart.set_line_mode(False)); self.b_line.clicked.connect(lambda: self.chart.set_line_mode(True))
        chips.addWidget(self.b_1d); chips.addWidget(self.b_5d); chips.addStretch(1); chips.addWidget(self.b_candle); chips.addWidget(self.b_line)
        dv.addLayout(chips)
        self.chart = ChartWidget(); dv.addWidget(self.chart, 3)
        self.hint = QLabel("Scroll to zoom · drag to pan · hover for bar details · double-click to reset · dashed = previous close · "
                           "shaded = pre/after hours · times ET")
        self.hint.setStyleSheet(f"color:{SUB};font-size:11px"); self.hint.setWordWrap(True)
        dv.addWidget(self.hint)
        hl = QLabel("Recent headlines"); hl.setStyleSheet("font-weight:600")
        dv.addWidget(hl)
        self.headlines = QTextBrowser(); self.headlines.setOpenLinks(False)
        self.headlines.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
        dv.addWidget(self.headlines, 2)
        split.addWidget(right)
        split.setSizes([620, 880])

        disc = QLabel(DISCLAIMER); disc.setStyleSheet(f"color:{SUB};font-size:11px"); disc.setWordWrap(True)
        v.addWidget(disc)

        self.bear = BearOverlay(root)
        self._set_detail_enabled(False)

        self.bus.scan_done.connect(self._on_scan)
        self.bus.scan_failed.connect(self._on_scan_failed)
        self.bus.chart_done.connect(self._on_chart)
        self.bus.chart_failed.connect(self._on_chart_failed)

        self.scan_timer = QTimer(self); self.scan_timer.timeout.connect(self._auto_scan)
        self.scan_timer.start(max(30, self.st.auto_refresh_seconds) * 1000)
        self.chart_timer = QTimer(self); self.chart_timer.timeout.connect(self._auto_chart)
        self.chart_timer.start(60_000)
        self.tray = None
        self._quitting = False
        self._apply_tray_setting()
        self._update_status()
        self.refresh_scan(initial=True)
        if args.celebrate:
            QTimer.singleShot(1500, lambda: self.bear.play([s.strip().upper() for s in args.celebrate.split(",") if s.strip()] or ["TEST"]))

    # ---------------- scan
    def _update_status(self):
        t = time.strftime("%I:%M %p", time.localtime(self.result.fetched_at)).lstrip("0") if self.result else "—"
        s = self.st
        self.status.setText(f"{D.market_label()} · updated {t} · ≥{s.min_pct_change:g}% · ≤${s.max_price:g} · "
                            f"≥{s.min_rel_vol:g}x vol · ≥{D.fmt_vol(s.min_volume)} shares")

    def refresh_scan(self, initial=False):
        if self.scanning:
            return
        self.scanning = True
        self.status.setText(self.status.text().split(" · ")[0] + " · scanning…")
        st = self.st
        def work():
            try:
                self.bus.scan_done.emit(D.run_scan(self.client, st), initial)
            except Exception as e:
                self.bus.scan_failed.emit(str(e) or type(e).__name__)
        threading.Thread(target=work, daemon=True).start()

    def _auto_scan(self):
        self._update_status()
        if D.should_poll(self.st.extended_hours):
            self.refresh_scan()

    def _on_scan(self, res: D.ScanResult, initial: bool):
        self.scanning = False
        self.result = res
        self.err.hide()
        lb = self.st.news_lookback_hours
        self.t_match.fill(res.matches, lb, self.selected)
        self.t_near.fill(res.near_misses, lb, self.selected)
        self.tabs.setTabText(0, f"All 4 criteria ({len(res.matches)})")
        self.tabs.setTabText(1, f"Near misses ({len(res.near_misses)})")
        self._update_status()
        syms = [h.symbol for h in res.matches]
        # bear: new on the list since the previous refresh (or first load with matches)
        now = set(syms); prev = self.prev_matches; self.prev_matches = now
        fresh_list = list(now if prev is None else now - prev)
        if fresh_list and self.st.bear_celebration:
            self.bear.play([s for s in syms if s in fresh_list])
        # toast: new today (first load just marks what's already there)
        unseen = D.new_unseen(syms)
        if unseen and not initial and self.st.toast_alerts:
            hits = [h for h in res.matches if h.symbol in unseen]
            title = f"New momentum stock: {hits[0].symbol}" if len(hits) == 1 else f"{len(hits)} new momentum stocks"
            toast.show(title, [f"{h.symbol} ${D.fmt_price(h.price)} +{h.pct_change:.0f}% {h.rel_vol:.1f}x vol – {h.catalyst.title}" for h in hits])
        if self.selected:
            self._fill_stats()
        if self.args.select and initial:
            self.select(self.args.select.upper())

    def _on_scan_failed(self, msg):
        self.scanning = False
        self.err.setText("⚠ " + msg); self.err.show()
        self._update_status()

    # ---------------- selection / detail
    def _hit(self, sym):
        if not self.result:
            return None
        return next((h for h in self.result.matches + self.result.near_misses if h.symbol == sym), None)

    def _table_selected(self, t):
        items = t.selectedItems()
        if items:
            self.select(items[0].data(Qt.UserRole))

    def _table_clicked(self, t, row):
        it = t.item(row, 0)
        if it:
            self.select(it.data(Qt.UserRole))

    def select(self, sym):
        if not sym or sym == self.selected:
            return
        self.selected = sym
        self.chart_data = None; self.news = []
        self._set_detail_enabled(True)
        self.d_sym.setText(sym)
        h = self._hit(sym)
        self.d_name.setText(h.name if h else "")
        self.chart.clear("Loading 5-minute bars…")
        self.headlines.setHtml(f"<span style='color:{SUB}'>Loading headlines…</span>")
        self._fill_stats()
        self.refresh_chart(with_news=True)

    def _set_detail_enabled(self, on):
        for w in (self.b_yahoo, self.b_dref, self.b_1d, self.b_5d, self.b_candle, self.b_line):
            w.setEnabled(on)
        if not on:
            self.d_sym.setText("No stock selected"); self.d_name.setText("Click a row on the left to see its chart")

    def _fill_stats(self):
        h = self._hit(self.selected); c = self.chart_data
        price = c.price if c and c.price == c.price else (h.price if h else float("nan"))
        pct = c.pct_change if c and c.pct_change == c.pct_change else (h.pct_change if h else float("nan"))
        vol = c.volume if c and c.volume else (h.volume if h else 0)
        col = GREEN if pct == pct and pct >= 0 else "#EF5350"
        parts = [f"<span style='font-size:26px;font-weight:700'>${D.fmt_price(price)}</span>",
                 f"<span style='font-size:18px;font-weight:600;color:{col}'>&nbsp;&nbsp;{'' if pct != pct else f'{pct:+.1f}%'}</span>",
                 f"<span style='color:{SUB}'>&nbsp;&nbsp;&nbsp;&nbsp;Volume</span> <b>{D.fmt_vol(vol)}</b>"]
        if h:
            parts.append(f"<span style='color:{SUB}'>&nbsp;&nbsp;&nbsp;Avg vol</span> <b>{D.fmt_vol(h.avg_volume)}</b>")
        parts.append(f"<span style='color:{SUB}'>&nbsp;&nbsp;&nbsp;Rel vol</span> <b>{f'{h.rel_vol:.1f}x' if h else '—'}</b>")
        if c and c.previous_close == c.previous_close:
            parts.append(f"<span style='color:{SUB}'>&nbsp;&nbsp;&nbsp;Prev close</span> <b>{D.fmt_price(c.previous_close)}</b>")
        if c:
            parts.append(f"<span style='color:{SUB}'>&nbsp;&nbsp;&nbsp;chart updated {time.strftime('%I:%M:%S %p', time.localtime(c.fetched_at)).lstrip('0')}</span>")
        self.d_stats.setText("".join(parts))
        if c and not self.d_name.text():
            self.d_name.setText(c.name)

    def set_range(self, rng):
        if rng == self.range:
            return
        self.range = rng
        if self.selected:
            self.chart.clear("Loading 5-minute bars…")
            self.refresh_chart(with_news=False)

    def refresh_chart(self, with_news=True):
        if not self.selected or self.chart_loading:
            return
        self.chart_loading = True
        sym, rng, ext = self.selected, self.range, self.st.extended_hours
        key = f"{sym}-{rng}"
        def work():
            try:
                cd = self.client.chart(sym, rng, ext)
                news = self.client.news(sym, 15) if with_news or not self.news else None
                self.bus.chart_done.emit(cd, news, key)
            except Exception as e:
                self.bus.chart_failed.emit(str(e) or type(e).__name__, key)
        threading.Thread(target=work, daemon=True).start()

    def _auto_chart(self):
        if self.selected and D.should_poll(self.st.extended_hours):
            self.refresh_chart(with_news=False)

    def _on_chart(self, cd: D.ChartData, news, key):
        self.chart_loading = False
        if key != f"{self.selected}-{self.range}":
            if self.selected:
                self.refresh_chart(with_news=True)
            return
        self.chart_data = cd
        self.chart.set_data(cd.bars, cd.previous_close, key)
        if news is not None:
            self.news = news
            self._fill_headlines()
        self._fill_stats()

    def _on_chart_failed(self, msg, key):
        self.chart_loading = False
        if key == f"{self.selected}-{self.range}":
            if self.chart_data is None:
                self.chart.clear("⚠ " + msg)
            if not self.news:
                self.headlines.setHtml(f"<span style='color:#EF5350'>⚠ {escape(msg)}</span>")

    def _fill_headlines(self):
        if not self.news:
            self.headlines.setHtml(f"<span style='color:{SUB}'>No headlines found.</span>")
            return
        cutoff = time.time() - self.st.news_lookback_hours * 3600
        rows = []
        for n in self.news:
            fresh = n.published >= cutoff and D.is_catalyst(n)
            col = GREEN if fresh else TEXT
            tag = f" · within {self.st.news_lookback_hours}h" if fresh else ""
            rows.append(f"<p style='margin:0 0 10px 0'><a href='{escape(n.link)}' style='color:{col};text-decoration:none;font-size:14px'>"
                        f"{escape(n.title)}</a><br><span style='color:{SUB};font-size:11px'>{escape(n.publisher)} · {D.ago(n.published)}{tag}</span></p>")
        self.headlines.setHtml("".join(rows))

    def open_yahoo(self):
        if self.selected:
            QDesktopServices.openUrl(QUrl(f"https://finance.yahoo.com/quote/{self.selected}"))

    # ---------------- tray / lifecycle
    def _apply_tray_setting(self):
        if self.st.scan_only_when_open:
            if self.tray is not None:
                self.tray.hide(); self.tray.deleteLater(); self.tray = None
            return
        if self.tray is None:
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip("Momentum Scanner – scanning during market hours")
            m = QMenu(self)
            m.addAction("Open", self.show_window)
            m.addAction("Scan now", lambda: self.refresh_scan())
            m.addSeparator()
            m.addAction("Quit", self.quit_app)
            self.tray.setContextMenu(m)
            self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_window()

    def show_window(self):
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_(); self.activateWindow()

    def quit_app(self):
        self._quitting = True
        D.log("quit")
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    def closeEvent(self, e):
        if not self._quitting and not self.st.scan_only_when_open and self.tray is not None:
            e.ignore(); self.hide()
            D.log(f"closed to tray (tray visible={self.tray.isVisible()}, still scanning)")
            return
        e.accept()
        D.log("window closed -> exit")
        self.quit_app()

    def handle_command(self, argv):
        """Commands forwarded from a second launch (single instance)."""
        D.log("second launch -> " + " ".join(argv))
        if "--quit" in argv:
            self.quit_app(); return
        self.show_window()
        if "--select" in argv and argv.index("--select") + 1 < len(argv):
            self.select(argv[argv.index("--select") + 1].upper())
        if "--celebrate" in argv:
            i = argv.index("--celebrate")
            syms = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else "TEST"
            self.bear.play([x.strip().upper() for x in syms.split(",") if x.strip()])
        if "--settings" in argv:
            QTimer.singleShot(0, self.open_settings)

    # ---------------- settings
    def open_settings(self):
        dlg = SettingsDialog(self.st, self)
        if dlg.exec() == QDialog.Accepted:
            self.st = dlg.result_settings(); self.st.save()
            D.log(f"settings saved (scan_only_when_open={self.st.scan_only_when_open})")
            self._apply_tray_setting()
            self.scan_timer.start(max(30, self.st.auto_refresh_seconds) * 1000)
            self.refresh_scan()
            if self.selected:
                self.refresh_chart(with_news=True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.bear.isVisible():
            self.bear.raise_()


STYLE = f"""
QWidget {{ background:{BG}; color:{TEXT}; font-family:'Segoe UI'; font-size:13px; }}
QFrame#detail {{ background:{CARD}; border-radius:10px; }}
QFrame#detail QLabel, QFrame#detail QPushButton#chip {{ background:transparent; }}
QPushButton {{ background:#1F2830; border:1px solid #2C3842; border-radius:8px; padding:6px 12px; }}
QPushButton:hover {{ background:#26323C; }}
QPushButton:disabled {{ color:#5A646E; }}
QPushButton#chip {{ border-radius:14px; padding:5px 14px; }}
QPushButton#chip:checked {{ background:#2E3A45; border-color:{GREEN}; color:{GREEN}; }}
QTabWidget::pane {{ border:0; }}
QTabBar::tab {{ background:transparent; color:{SUB}; padding:8px 18px; border-bottom:2px solid transparent; font-weight:600; }}
QTabBar::tab:selected {{ color:{GREEN}; border-bottom:2px solid {GREEN}; }}
QTableWidget {{ background:{CARD}; alternate-background-color:#1B2229; border:0; border-radius:8px;
               selection-background-color:#1E3A2E; selection-color:{TEXT}; }}
QHeaderView::section {{ background:{CARD}; color:{SUB}; border:0; padding:6px; font-weight:600; }}
QTextBrowser {{ background:transparent; border:0; }}
QToolTip {{ background:#202830; color:white; border:1px solid #2C3842; }}
QDialog {{ background:{CARD}; }}
QDoubleSpinBox, QSpinBox {{ background:#1F2830; border:1px solid #2C3842; border-radius:6px; padding:4px; }}
QSplitter::handle {{ background:{BG}; width:8px; }}
QCheckBox {{ spacing:10px; background:transparent; }}
QCheckBox::indicator {{ width:18px; height:18px; border:2px solid #8A98A6; border-radius:4px; background:#1F2830; }}
QCheckBox::indicator:hover {{ border-color:{GREEN}; }}
QCheckBox::indicator:checked {{ background:{GREEN}; border-color:{GREEN}; image:url(__CHECK_SVG__); }}
QCheckBox::indicator:disabled {{ border-color:#4A545E; background:#161B20; }}
QCheckBox::indicator:checked:disabled {{ background:#3A6B55; border-color:#3A6B55; }}
"""


def _check_svg_path() -> str:
    """Render the check-mark image used by the checkbox stylesheet (dark tick for the green box).
    Writes check.png plus @2x/@3x variants so it stays crisp on high-DPI screens. Needs a QApplication."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF
    base = os.path.join(D.APP_DIR, "check")
    for scale, suffix in ((1, ""), (2, "@2x"), (3, "@3x")):
        size = 18 * scale
        pm = QPixmap(size, size); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#0B1A12"), 2.6 * scale); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(4 * scale, 9.5 * scale), QPointF(7.5 * scale, 13 * scale), QPointF(14 * scale, 5.5 * scale)]))
        p.end()
        pm.save(base + suffix + ".png", "PNG")
    return (base + ".png").replace("\\", "/")


def main():
    ap = argparse.ArgumentParser(prog="MomentumScanner")
    ap.add_argument("--celebrate", nargs="?", const="TEST", help="play the bear celebration (testing), e.g. --celebrate JZ,QNME")
    ap.add_argument("--select", help="select this symbol after the first scan (testing)")
    ap.add_argument("--quit", action="store_true", help="tell a running instance to quit")
    ap.add_argument("--settings", action="store_true", help="open the settings dialog")
    args, _ = ap.parse_known_args()
    icon_file = os.path.join(os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__), "momentum.ico")
    toast.register(icon_file)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Momentum Scanner")
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in ((QPalette.Window, BG), (QPalette.Base, CARD), (QPalette.Text, TEXT), (QPalette.WindowText, TEXT),
                      (QPalette.Button, "#1F2830"), (QPalette.ButtonText, TEXT), (QPalette.Highlight, "#1E3A2E"),
                      (QPalette.HighlightedText, TEXT), (QPalette.ToolTipBase, "#202830"), (QPalette.ToolTipText, "white")):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(STYLE.replace("__CHECK_SVG__", _check_svg_path()))
    app.setQuitOnLastWindowClosed(False)   # closing is handled in MainWindow.closeEvent

    # single instance: forward our arguments to a running copy and exit
    server_name = "MomentumScanner-" + os.environ.get("USERNAME", "user")
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if sock.waitForConnected(500):
        sock.write("\x1f".join(sys.argv[1:] or ["--show"]).encode("utf-8"))
        sock.flush(); sock.waitForBytesWritten(1000); sock.disconnectFromServer()
        sys.exit(0)
    if args.quit:
        sys.exit(0)
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    w = MainWindow(args)
    def on_conn():
        c = server.nextPendingConnection()
        def read():
            raw = bytes(c.readAll()).decode("utf-8", "ignore")
            w.handle_command([a for a in raw.split("\x1f") if a])
        c.readyRead.connect(read)
        if c.bytesAvailable():
            read()
    server.newConnection.connect(on_conn)
    w.show()
    if args.settings:
        QTimer.singleShot(800, w.open_settings)
    D.log("started")
    sys.exit(app.exec())
