"""數據下載：yfinance 為主，Stooq 為後備；--synthetic 用隨機數據做離線測試。

輸出 Panel：open/high/low/close/volume 五個闊表（index=日期，columns=股票）。
價格已按拆股及派息調整（auto_adjust），模擬成交用調整後價格。
"""
from __future__ import annotations

import io
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
FIELDS = ["open", "high", "low", "close", "volume"]


@dataclass
class Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    source: dict = field(default_factory=dict)      # symbol -> 'yfinance' / 'stooq'
    missing: list = field(default_factory=list)

    @property
    def symbols(self):
        return list(self.close.columns)


def read_universe(filename: str) -> list[str]:
    syms = []
    for line in (HERE / filename).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        syms.extend(line.split())
    return list(dict.fromkeys(syms))


# ---------------------------------------------------------------- yfinance
def _yf_download(symbols, start, retries=3):
    import yfinance as yf
    last_err = None
    for attempt in range(retries):
        try:
            df = yf.download(symbols, start=start, auto_adjust=True, progress=False,
                             group_by="column", threads=True)
            if df is not None and not df.empty:
                return df
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(5 * (attempt + 1))
    print(f"[data] yfinance 失敗: {last_err}")
    return None


def _stooq_one(sym, start):
    """Stooq 後備：美股 aapl.us；加股暫不支援。"""
    if sym.endswith(".TO") or sym.endswith("=X"):
        return None
    code = sym.lower().replace(".", "-") + ".us"
    url = f"https://stooq.com/q/d/l/?s={code}&i=d"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            txt = r.read().decode()
        df = pd.read_csv(io.StringIO(txt), parse_dates=["Date"], index_col="Date")
        if df.empty or "Close" not in df:
            return None
        df = df[df.index >= pd.Timestamp(start)]
        return df.rename(columns=str.lower)[FIELDS]
    except Exception:  # noqa: BLE001
        return None


def fetch(symbols: list[str], start: str) -> Panel:
    frames, source = {}, {}
    # 分批下載，減低被 Yahoo 限流嘅機會
    for i in range(0, len(symbols), 50):
        raw = _yf_download(symbols[i:i + 50], start)
        if raw is None:
            continue
        for f in FIELDS:
            key = f.capitalize()
            if key in raw.columns.get_level_values(0):
                part = raw[key]
                frames[f] = part.copy() if f not in frames else frames[f].join(part, how="outer")
        time.sleep(2)
    for f in FIELDS:
        frames.setdefault(f, pd.DataFrame())
    close = frames["close"]
    for s in symbols:
        if s in close.columns and close[s].notna().sum() > 260:
            source[s] = "yfinance"
    # 後備
    for s in symbols:
        if s in source:
            continue
        df = _stooq_one(s, start)
        if df is not None and len(df) > 260:
            for f in FIELDS:
                frames[f] = frames[f].reindex(frames[f].index.union(df.index))
                frames[f][s] = df[f]
            source[s] = "stooq"
    ok = [s for s in symbols if s in source]
    missing = [s for s in symbols if s not in source]
    idx = frames["close"].index
    p = Panel(**{f: frames[f].reindex(index=idx, columns=ok).astype(float) for f in FIELDS},
              source=source, missing=missing)
    _clean(p)
    return p


def fetch_series(symbol: str, start: str) -> pd.Series | None:
    p = fetch([symbol], start)
    if symbol in p.close.columns:
        return p.close[symbol].dropna()
    return None


def _clean(p: Panel):
    # 只保留有成交嘅交易日（全部股票都冇數據嘅日子刪走）
    valid = p.close.notna().any(axis=1)
    for f in FIELDS:
        setattr(p, f, getattr(p, f).loc[valid])
    p.volume = p.volume.fillna(0)


# ---------------------------------------------------------------- synthetic
def synthetic(symbols: list[str], start: str, end: str | None = None, seed=7) -> Panel:
    """離線測試用：幾何布朗運動 + 隨機整固/突破段，令策略有機會觸發。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, end or pd.Timestamp.today().normalize())
    n = len(idx)
    out = {f: {} for f in FIELDS}
    for s in symbols:
        mu = rng.normal(0.0004, 0.0004)
        vol = rng.uniform(0.012, 0.035)
        r = rng.normal(mu, vol, n)
        # 插入幾段「整固 → 突破」
        for _ in range(rng.integers(3, 9)):
            a = rng.integers(260, max(261, n - 80))
            r[a:a + 35] *= 0.35
            r[a:a + 35] -= r[a:a + 35].mean()
            r[a + 35:a + 60] += rng.uniform(0.003, 0.008)
        c = 50 * np.exp(np.cumsum(r)) * rng.uniform(0.5, 6)
        o = c * np.exp(rng.normal(0, vol * 0.3, n))
        h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, vol * 0.5, n)))
        lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, vol * 0.5, n)))
        v = rng.lognormal(14, 0.4, n) * (1 + 3 * (np.abs(r) > 2 * vol))
        for f, arr in zip(FIELDS, [o, h, lo, c, v]):
            out[f][s] = arr
    p = Panel(**{f: pd.DataFrame(out[f], index=idx) for f in FIELDS},
              source={s: "synthetic" for s in symbols})
    return p


def synthetic_fx(start, end=None):
    idx = pd.bdate_range(start, end or pd.Timestamp.today().normalize())
    rng = np.random.default_rng(1)
    return pd.Series(1.35 * np.exp(np.cumsum(rng.normal(0, 0.003, len(idx)))), index=idx)
