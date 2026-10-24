"""Market data (Yahoo Finance, keyless), scanner logic, market hours and settings."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time as dtime
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MomentumScanner")
os.makedirs(APP_DIR, exist_ok=True)

# ---------------------------------------------------------------- settings
@dataclass
class Settings:
    min_pct_change: float = 25.0
    max_price: float = 5.0
    min_rel_vol: float = 2.0
    min_volume: int = 500_000
    news_lookback_hours: int = 24
    include_otc: bool = False
    time_adjusted_rel_vol: bool = True
    extended_hours: bool = False
    auto_refresh_seconds: int = 60
    toast_alerts: bool = True
    scan_only_when_open: bool = True
    bear_celebration: bool = True

    @staticmethod
    def path() -> str:
        return os.path.join(APP_DIR, "settings.json")

    @classmethod
    def load(cls) -> "Settings":
        try:
            with open(cls.path(), encoding="utf-8") as f:
                raw = json.load(f)
            known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
            return cls(**known)
        except Exception:
            return cls()

    def save(self) -> None:
        with open(self.path(), "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)


# ---------------------------------------------------------------- market hours
HOLIDAYS = {date.fromisoformat(d) for d in [
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
]}
PRE_OPEN, OPEN, CLOSE, POST_CLOSE = dtime(4, 0), dtime(9, 30), dtime(16, 0), dtime(20, 0)


def now_et() -> datetime:
    return datetime.now(ET)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def session(now: Optional[datetime] = None) -> str:
    t = (now or now_et()).astimezone(ET)
    if not is_trading_day(t.date()):
        return "closed"
    lt = t.time()
    if lt < PRE_OPEN:
        return "closed"
    if lt < OPEN:
        return "pre"
    if lt < CLOSE:
        return "regular"
    if lt < POST_CLOSE:
        return "post"
    return "closed"


def should_poll(extended: bool) -> bool:
    s = session()
    return s == "regular" or (extended and s in ("pre", "post"))


def regular_fraction() -> float:
    t = now_et()
    if session(t) != "regular":
        return 1.0
    mins = (t.hour * 60 + t.minute + t.second / 60) - (9 * 60 + 30)
    return min(max(mins / 390.0, 0.0), 1.0)


def market_label() -> str:
    return {"pre": "Pre-market", "regular": "Market open", "post": "After hours"}.get(
        session(), "Market closed – showing last session")


def log(msg: str) -> None:
    try:
        with open(os.path.join(APP_DIR, "app.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def trading_day_key() -> str:
    return now_et().date().isoformat()


def is_regular_bar(ts: int) -> bool:
    t = datetime.fromtimestamp(ts, ET).time()
    return OPEN <= t < CLOSE


# ---------------------------------------------------------------- models
@dataclass
class NewsItem:
    title: str
    publisher: str
    link: str
    published: int  # epoch seconds


@dataclass
class StockHit:
    symbol: str
    name: str
    exchange: str
    price: float
    pct_change: float
    volume: int
    avg_volume: int
    rel_vol: float
    catalyst: Optional[NewsItem]
    latest_news: Optional[NewsItem]


@dataclass
class ScanResult:
    matches: list
    near_misses: list
    fetched_at: float
    scanned: int


@dataclass
class Bar:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: int


@dataclass
class ChartData:
    symbol: str
    name: str
    exchange: str
    price: float
    previous_close: float
    volume: int
    bars: list = field(default_factory=list)
    range: str = "1d"
    fetched_at: float = 0.0

    @property
    def pct_change(self) -> float:
        if self.previous_close and self.price == self.price:
            return (self.price - self.previous_close) / self.previous_close * 100
        return float("nan")


class YahooError(Exception):
    pass


# ---------------------------------------------------------------- Yahoo client
class YahooClient:
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
    MAJOR_EXCHANGES = ["NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS"]

    def __init__(self):
        self._lock = threading.Lock()
        self._new_session()
        self.crumb: Optional[str] = None

    def _new_session(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": self.UA, "Accept": "*/*"})

    def ensure_crumb(self, force: bool = False) -> str:
        with self._lock:
            if self.crumb and not force:
                return self.crumb
            if force:
                self._new_session()
            try:  # answers 404 but sets the session cookie the crumb is tied to
                self.s.get("https://fc.yahoo.com/", timeout=10)
            except requests.RequestException:
                pass
            r = self.s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10)
            body = r.text.strip()
            if r.status_code == 429:
                raise YahooError("Yahoo rate limit hit (429). Try again in a minute.")
            if r.status_code != 200 or not body or "<" in body or " " in body:
                raise YahooError(f"Could not get Yahoo session (HTTP {r.status_code}).")
            self.crumb = body
            return body

    def _request(self, method: str, url: str, **kw) -> dict:
        for attempt in range(2):
            crumb = self.ensure_crumb(force=attempt > 0)
            params = dict(kw.pop("params", {}) or {})
            params["crumb"] = crumb
            r = self.s.request(method, url, params=params, timeout=20, **kw)
            if r.status_code in (401, 403) or "Invalid Crumb" in r.text[:200]:
                if attempt == 0:
                    kw["params"] = {k: v for k, v in params.items() if k != "crumb"}
                    continue
            if r.status_code == 429:
                raise YahooError("Yahoo rate limit hit (429). Try again in a minute.")
            if r.status_code != 200:
                raise YahooError(f"Yahoo request failed (HTTP {r.status_code}).")
            return r.json()
        raise YahooError("Yahoo session rejected.")

    def screen(self, st: Settings) -> list:
        def op(o, f, v):
            return {"operator": o, "operands": [f, v]}
        operands = [
            op("gte", "percentchange", st.min_pct_change),
            op("lte", "intradayprice", st.max_price),
            op("gt", "intradayprice", 0),
            op("gte", "dayvolume", st.min_volume),
            op("eq", "region", "us"),
        ]
        if not st.include_otc:
            operands.append({"operator": "or", "operands": [op("eq", "exchange", e) for e in self.MAJOR_EXCHANGES]})
        body = {"size": 100, "offset": 0, "sortField": "percentchange", "sortType": "DESC",
                "quoteType": "EQUITY", "query": {"operator": "and", "operands": operands},
                "userId": "", "userIdType": "guid"}
        j = self._request("POST", "https://query2.finance.yahoo.com/v1/finance/screener",
                          params={"lang": "en-US", "region": "US", "formatted": "false"}, json=body)
        res = ((j.get("finance") or {}).get("result") or [None])[0]
        if res is None:
            raise YahooError("Screener returned no result.")
        return res.get("quotes") or []

    def news(self, symbol: str, count: int = 10) -> list:
        items: list[NewsItem] = []
        try:
            r = self.s.get("https://query2.finance.yahoo.com/v1/finance/search",
                           params={"q": symbol, "newsCount": count, "quotesCount": 0,
                                   "enableFuzzyQuery": "false"}, timeout=15)
            if r.status_code == 200:
                for n in r.json().get("news") or []:
                    rel = n.get("relatedTickers") or []
                    if rel and symbol.upper() not in [x.upper() for x in rel]:
                        continue
                    items.append(NewsItem(n.get("title", ""), n.get("publisher", ""),
                                          n.get("link", ""), int(n.get("providerPublishTime") or 0)))
        except Exception:
            pass
        try:
            r = self.s.get("https://feeds.finance.yahoo.com/rss/2.0/headline",
                           params={"s": symbol, "region": "US", "lang": "en-US"}, timeout=15)
            if r.status_code == 200:
                root = ElementTree.fromstring(r.content)
                for it in root.iter("item"):
                    title = (it.findtext("title") or "").strip()
                    link = (it.findtext("link") or "").split("?.tsrc")[0].strip()
                    try:
                        ts = int(parsedate_to_datetime(it.findtext("pubDate") or "").timestamp())
                    except Exception:
                        ts = 0
                    items.append(NewsItem(title, "Yahoo Finance", link, ts))
        except Exception:
            pass
        seen, out = set(), []
        for n in sorted(items, key=lambda x: -x.published):
            k = n.title.lower()
            if n.title and n.link.startswith("http") and k not in seen:
                seen.add(k)
                out.append(n)
        return out

    def chart(self, symbol: str, rng: str, include_pre_post: bool) -> ChartData:
        j = self._request("GET", f"https://query2.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}",
                          params={"interval": "5m", "range": rng,
                                  "includePrePost": str(include_pre_post).lower(), "events": "div,splits"})
        ch = j.get("chart") or {}
        res = (ch.get("result") or [None])[0]
        if not res:
            raise YahooError(((ch.get("error") or {}).get("description")) or f"No chart data for {symbol}")
        meta = res.get("meta") or {}
        ts = res.get("timestamp") or []
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        bars = []
        for i, t in enumerate(ts):
            try:
                o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            except (KeyError, IndexError):
                continue
            if None in (o, h, l, c):
                continue
            v = (q.get("volume") or [0] * len(ts))[i] or 0
            bars.append(Bar(int(t), float(o), float(h), float(l), float(c), int(v)))
        prev = meta.get("previousClose") or meta.get("chartPreviousClose") or float("nan")
        return ChartData(
            symbol=meta.get("symbol", symbol),
            name=meta.get("longName") or meta.get("shortName") or symbol,
            exchange=meta.get("fullExchangeName", ""),
            price=float(meta.get("regularMarketPrice") or float("nan")),
            previous_close=float(prev),
            volume=int(meta.get("regularMarketVolume") or 0),
            bars=bars, range=rng, fetched_at=time.time())


# ---------------------------------------------------------------- scanner
ROUNDUP = re.compile(
    r"most active stocks|premarket (gainers|decliners|movers)|top (premarket |midday |after-hours )?"
    r"(gainers|losers|decliners|movers)|stocks? (moving|movers|making the biggest moves)|midday movers|"
    r"after-hours movers|biggest movers|penny stocks to watch", re.I)


def is_catalyst(n: NewsItem) -> bool:
    return not ROUNDUP.search(n.title)


def expected_volume_fraction() -> float:
    return max(regular_fraction() ** 0.8, 0.05)


def run_scan(client: YahooClient, st: Settings) -> ScanResult:
    raw = client.screen(st)
    frac = expected_volume_fraction() if st.time_adjusted_rel_vol else 1.0
    cands = []
    for q in raw:
        try:
            price = float(q["regularMarketPrice"]); pct = float(q["regularMarketChangePercent"])
        except (KeyError, TypeError, ValueError):
            continue
        vol = int(q.get("regularMarketVolume") or 0)
        avg = int(q.get("averageDailyVolume3Month") or 0) or int(q.get("averageDailyVolume10Day") or 0)
        if avg <= 0:
            continue
        rv = vol / (avg * frac)
        if pct < st.min_pct_change or price > st.max_price or vol < st.min_volume or rv < st.min_rel_vol:
            continue
        cands.append((q, price, pct, vol, avg, rv))
    cutoff = time.time() - st.news_lookback_hours * 3600

    def build(c):
        q, price, pct, vol, avg, rv = c
        sym = q["symbol"]
        try:
            news = client.news(sym)
        except Exception:
            news = []
        cat = next((n for n in news if n.published >= cutoff and is_catalyst(n)), None)
        latest = next((n for n in news if is_catalyst(n)), news[0] if news else None)
        return StockHit(sym, q.get("shortName") or q.get("longName") or sym,
                        q.get("fullExchangeName") or q.get("exchange", ""),
                        price, pct, vol, avg, rv, cat, latest)

    with ThreadPoolExecutor(max_workers=4) as ex:
        hits = list(ex.map(build, cands))
    hits.sort(key=lambda h: -h.pct_change)
    return ScanResult([h for h in hits if h.catalyst], [h for h in hits if not h.catalyst], time.time(), len(raw))


# ---------------------------------------------------------------- seen store (one alert per stock per day)
def _seen_path() -> str:
    return os.path.join(APP_DIR, "seen.json")


def new_unseen(symbols: list, mark: bool = True) -> list:
    day = trading_day_key()
    try:
        with open(_seen_path(), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    seen = set(data.get("symbols", [])) if data.get("day") == day else set()
    fresh = [s for s in symbols if s not in seen]
    if mark and fresh:
        with open(_seen_path(), "w", encoding="utf-8") as f:
            json.dump({"day": day, "symbols": sorted(seen | set(fresh))}, f)
    return fresh


def fmt_vol(v: int) -> str:
    if v >= 1e9:
        return f"{v / 1e9:.2f}B"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}K"
    return str(v)


def fmt_price(p: float) -> str:
    if p != p:
        return "—"
    return f"{p:.4f}" if abs(p) < 1 else f"{p:.2f}"


def ago(ts: int) -> str:
    mins = int((time.time() - ts) / 60)
    if mins < 60:
        return f"{mins}m ago"
    if mins < 48 * 60:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"
