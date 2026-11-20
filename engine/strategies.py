"""指標同 A/B/C 入場條件（向量化計算）。每個條件獨立保存，方便網站顯示「差邊一步」。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import LIQUIDITY

COND_LABELS = {
    "liquid": "流動性",
    "breakout": "突破",
    "volume": "量能",
    "trend": "趨勢",
    "rs": "相對強度",
    "base": "整固箱體",
    "near_high": "接近 52 週高位",
}


class Indicators:
    def __init__(self, p, bench_close: pd.Series):
        c, h, l, v = p.close, p.high, p.low, p.volume
        self.close, self.high, self.low, self.open = c, h, l, p.open
        prev_c = c.shift(1)
        tr = np.maximum(h - l, np.maximum((h - prev_c).abs(), (l - prev_c).abs()))
        self.atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        self.ma = {n: c.rolling(n, min_periods=n).mean() for n in (20, 50, 150, 200)}
        self.dollar_vol = (c * v).rolling(20, min_periods=20).mean().shift(1)
        self._v = v
        self._vavg = {}
        self._hh = {}
        self._ret = {}
        self.bench = bench_close.reindex(c.index).ffill()
        self.bench_ma200 = self.bench.rolling(200, min_periods=200).mean()
        self.high252 = h.rolling(252, min_periods=200).max()

    def vavg(self, n):
        if n not in self._vavg:
            self._vavg[n] = self._v.rolling(n, min_periods=n).mean().shift(1)
        return self._vavg[n]

    def hh(self, n):
        if n not in self._hh:
            self._hh[n] = self.high.rolling(n, min_periods=n).max().shift(1)
        return self._hh[n]

    def rs(self, n):
        if n not in self._ret:
            r = self.close / self.close.shift(n) - 1
            br = self.bench / self.bench.shift(n) - 1
            self._ret[n] = r.sub(br, axis=0)
        return self._ret[n]

    def regime_ok(self) -> pd.Series:
        return self.bench > self.bench_ma200


def conditions(ind: Indicators, cfg: dict) -> dict[str, pd.DataFrame]:
    c = ind.close
    conds = {}
    conds["liquid"] = (c >= LIQUIDITY["min_price"]) & (ind.dollar_vol >= LIQUIDITY["min_dollar_vol"])

    if cfg["base"]:
        b = cfg["base"]
        bh = ind.high.rolling(b["days"], min_periods=b["days"]).max().shift(1)
        bl = ind.low.rolling(b["days"], min_periods=b["days"]).min().shift(1)
        width = (bh - bl) / bh
        allowed = (b["atr_mult"] * ind.atr.shift(1) / c.shift(1)).clip(b["min_pct"], b["max_pct"])
        conds["base"] = width <= allowed
        conds["breakout"] = c > bh
        ref = bh
    else:
        ref = ind.hh(cfg["breakout_days"])
        conds["breakout"] = c > ref

    conds["volume"] = ind._v >= cfg["vol_mult"] * ind.vavg(cfg["vol_avg_days"])

    t = cfg["trend"]
    if t == "ma20>ma50":
        conds["trend"] = (c > ind.ma[20]) & (ind.ma[20] > ind.ma[50])
    elif t == "ma50>ma200":
        conds["trend"] = (c > ind.ma[50]) & (ind.ma[50] > ind.ma[200])
    else:
        conds["trend"] = c > ind.ma[200]

    conds["rs"] = ind.rs(cfg["rs_days"]) > cfg["rs_min"]

    if cfg["near_high"]:
        conds["near_high"] = c >= ind.high252 * (1 - cfg["near_high"])

    for k in conds:
        conds[k] = conds[k].fillna(False)
    conds["_ref"] = ref
    return conds


def signals(conds: dict) -> pd.DataFrame:
    keys = [k for k in conds if not k.startswith("_")]
    out = conds[keys[0]].copy()
    for k in keys[1:]:
        out &= conds[k]
    return out


def score(ind: Indicators, cfg: dict) -> pd.DataFrame:
    """排名：相對強度高者優先。"""
    return ind.rs(cfg["rs_days"])


def explain_day(conds: dict, sc: pd.DataFrame, ind: Indicators, date, top=8):
    """某日嘅「差少少就合格」名單：已突破但未過其他條件，或其他都過但未突破。"""
    keys = [k for k in conds if not k.startswith("_")]
    row = {k: conds[k].loc[date] for k in keys}
    df = pd.DataFrame(row)
    df["n_pass"] = df[keys].sum(axis=1)
    df["score"] = sc.loc[date]
    near = df[(df["n_pass"] >= len(keys) - 1) & df["liquid"]].sort_values(
        ["n_pass", "score"], ascending=False).head(top)
    items = []
    for sym, r in near.iterrows():
        failed = [COND_LABELS[k] for k in keys if not r[k]]
        ref = conds["_ref"].loc[date, sym]
        items.append({
            "symbol": sym,
            "close": _f(ind.close.loc[date, sym]),
            "ref": _f(ref),
            "passed": int(r["n_pass"]), "total": len(keys),
            "failed": failed,
            "rs": _f(r["score"], 4),
        })
    return items


def _f(x, nd=2):
    try:
        x = float(x)
        return None if np.isnan(x) else round(x, nd)
    except (TypeError, ValueError):
        return None
