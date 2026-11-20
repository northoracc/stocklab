"""組合模擬器：收市決策、翌日開市執行。

設計重點：每次都由起始日「重新播放」到今日，所以唔需要儲存狀態；
就算某日 GitHub Actions 冇跑到，下一次都會自動補返，唔會再出現「更新卡死」。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import ACCOUNT, COSTS, REGIME


@dataclass
class Position:
    symbol: str
    shares: float
    entry: float
    entry_date: str
    stop: float
    risk_per_share: float
    highest: float
    cost_in: float


@dataclass
class Book:
    cash: float
    positions: dict = field(default_factory=dict)
    pending_buys: list = field(default_factory=list)   # dict(symbol, signal_close, atr, date)
    pending_sells: dict = field(default_factory=dict)  # symbol -> reason
    trades: list = field(default_factory=list)
    events: dict = field(default_factory=dict)          # date -> [event]
    equity: list = field(default_factory=list)          # (date, equity_local, equity_cad)

    def log(self, date, **ev):
        self.events.setdefault(date, []).append(ev)


def _shares(x):
    return x if ACCOUNT["fractional"] else math.floor(x)


def run(ind, conds, sig, sc, cfg, start, fx: pd.Series | None, end=None):
    """回傳 Book。fx = CAD per USD（美股用），加股為 None。"""
    dates = ind.close.loc[start:end].index
    if len(dates) == 0:
        raise ValueError("冇交易日數據")
    fx = fx.reindex(ind.close.index).ffill().bfill() if fx is not None else None
    init_cad = ACCOUNT["initial_cad"]
    if fx is not None:
        cash0 = init_cad * (1 - COSTS["fx_conversion_pct"]) / fx.loc[dates[0]]
    else:
        cash0 = init_cad
    book = Book(cash=cash0)
    if fx is not None:
        book.log(str(dates[0].date()), type="fx",
                 text=f"起始 CAD {init_cad:,.0f} 一次過換成 USD {cash0:,.2f}（匯率 {fx.loc[dates[0]]:.4f}，成本 {COSTS['fx_conversion_pct']:.1%}）")

    C, O, ATR = ind.close, ind.open, ind.atr
    regime = ind.regime_ok()
    comm, slip = COSTS["commission"], COSTS["slippage_pct"]
    last_close = {}
    prev_equity = cash0

    for t in dates:
        d = str(t.date())
        crow, orow = C.loc[t], O.loc[t]

        # ---------- 1. 開市：先賣後買
        for sym, reason in list(book.pending_sells.items()):
            px = orow.get(sym)
            if px is None or np.isnan(px):
                continue  # 停牌／缺數據 → 留待下日
            pos = book.positions.pop(sym)
            fill = px * (1 - slip)
            proceeds = pos.shares * fill - comm
            book.cash += proceeds
            pnl = proceeds - pos.cost_in
            r_mult = pnl / (pos.risk_per_share * pos.shares) if pos.risk_per_share > 0 else 0
            hold = int(np.busday_count(pos.entry_date, d))
            book.trades.append(dict(symbol=sym, entry_date=pos.entry_date, exit_date=d,
                                    entry=round(pos.entry, 4), exit=round(fill, 4),
                                    shares=round(pos.shares, 4), pnl=round(pnl, 2),
                                    r=round(r_mult, 2), days=hold, reason=reason))
            book.log(d, type="sell", symbol=sym, price=round(fill, 2), shares=round(pos.shares, 4),
                     pnl=round(pnl, 2), r=round(r_mult, 2), reason=reason)
            del book.pending_sells[sym]

        for order in book.pending_buys:
            sym = order["symbol"]
            px = orow.get(sym)
            if px is None or np.isnan(px):
                book.log(d, type="skip", symbol=sym, reason="開市冇報價")
                continue
            if px > order["signal_close"] * (1 + cfg["max_gap"]):
                book.log(d, type="skip", symbol=sym,
                         reason=f"跳空高開 {px / order['signal_close'] - 1:+.1%}，超過上限 {cfg['max_gap']:.0%}")
                continue
            if len(book.positions) >= ACCOUNT["max_positions"]:
                book.log(d, type="skip", symbol=sym, reason="持倉已滿")
                continue
            fill = px * (1 + slip)
            stop_dist = cfg["atr_stop"] * order["atr"]
            mult = 1.0 if order["regime_ok"] else REGIME["below_ma200_size_mult"]
            risk_amt = prev_equity * ACCOUNT["risk_per_trade_pct"] * mult
            n = min(risk_amt / stop_dist,
                    prev_equity * ACCOUNT["max_position_pct"] / fill,
                    (book.cash - comm) / fill)
            n = _shares(max(n, 0))
            value = n * fill
            if value < ACCOUNT["min_order_value"]:
                book.log(d, type="skip", symbol=sym,
                         reason="資金不足或金額太細" if ACCOUNT["fractional"] else "一股都超出可用額度")
                continue
            rt_cost = 2 * comm + 2 * slip * value
            if rt_cost > COSTS["max_cost_to_risk"] * n * stop_dist:
                book.log(d, type="skip", symbol=sym,
                         reason=f"來回成本 {rt_cost:.2f} 超過風險 {COSTS['max_cost_to_risk']:.0%}")
                continue
            book.cash -= value + comm
            book.positions[sym] = Position(sym, n, fill, d, fill - stop_dist, stop_dist,
                                           fill, value + comm)
            book.log(d, type="buy", symbol=sym, price=round(fill, 2), shares=round(n, 4),
                     value=round(value, 2), stop=round(fill - stop_dist, 2),
                     risk=round(n * stop_dist, 2), half=not order["regime_ok"])
        book.pending_buys = []

        # ---------- 2. 收市：更新止損、決定退出
        for sym, pos in book.positions.items():
            c = crow.get(sym)
            if c is None or np.isnan(c):
                continue
            last_close[sym] = c
            if sym in book.pending_sells:
                continue
            atr = ATR.at[t, sym]
            if not np.isnan(atr) and d != pos.entry_date:
                pos.highest = max(pos.highest, c)
                pos.stop = max(pos.stop, pos.highest - cfg["atr_stop"] * atr)
            reason = None
            if c < pos.stop:
                reason = f"收市 {c:.2f} 跌穿追蹤止損 {pos.stop:.2f}"
            elif cfg.get("exit_ma") and c < ind.ma[cfg["exit_ma"]].at[t, sym]:
                reason = f"收市跌穿 MA{cfg['exit_ma']}"
            elif cfg["time_stop_days"]:
                held = np.busday_count(pos.entry_date, d)
                if held >= cfg["time_stop_days"] and (c - pos.entry) < cfg["time_stop_min_r"] * pos.risk_per_share:
                    reason = f"持有 {held} 日仍未升夠 {cfg['time_stop_min_r']}R（時間止損）"
            if reason:
                book.pending_sells[sym] = reason
                book.log(d, type="exit_signal", symbol=sym, reason=reason)

        # ---------- 3. 收市：新訊號
        free = ACCOUNT["max_positions"] - len(book.positions) + len(book.pending_sells)
        todays = sig.loc[t]
        cands = [s for s in todays[todays].index
                 if s not in book.positions and s in crow.index and not np.isnan(ATR.at[t, s])]
        cands.sort(key=lambda s: -(sc.at[t, s] if not np.isnan(sc.at[t, s]) else -9))
        reg = bool(regime.loc[t]) if not pd.isna(regime.loc[t]) else True
        for rank, s in enumerate(cands):
            if rank < free:
                book.pending_buys.append(dict(symbol=s, signal_close=float(crow[s]),
                                              atr=float(ATR.at[t, s]), date=d, regime_ok=reg))
                book.log(d, type="signal", symbol=s, close=round(float(crow[s]), 2),
                         rank=rank + 1, half=not reg)
            else:
                book.log(d, type="signal_unfilled", symbol=s, reason="持倉名額不足，排名較低")

        # ---------- 4. 淨值
        mv = 0.0
        for sym, pos in book.positions.items():
            px = last_close.get(sym, pos.entry)
            mv += pos.shares * px
        eq = book.cash + mv
        prev_equity = eq
        eq_cad = eq * fx.loc[t] if fx is not None else eq
        book.equity.append((d, round(eq, 2), round(eq_cad, 2)))

    book.last_close = last_close
    return book


# ---------------------------------------------------------------- 統計
def stats(book: Book, bench: pd.Series, start, init_cad):
    eq = pd.Series([e[2] for e in book.equity], index=pd.to_datetime([e[0] for e in book.equity]))
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = eq.iloc[-1] / init_cad - 1
    dd = (eq / eq.cummax() - 1).min()
    tr = pd.DataFrame(book.trades)
    b = bench.reindex(eq.index).ffill()
    btotal = b.iloc[-1] / b.iloc[0] - 1
    out = dict(
        start=str(eq.index[0].date()), end=str(eq.index[-1].date()),
        final_cad=round(eq.iloc[-1], 2), total_return=round(total, 4),
        cagr=round((1 + total) ** (1 / years) - 1, 4) if years > 0.5 else None,
        max_drawdown=round(dd, 4), trades=int(len(tr)),
        bench_return=round(btotal, 4),
        bench_cagr=round((1 + btotal) ** (1 / years) - 1, 4) if years > 0.5 else None,
    )
    if len(tr):
        wins = tr[tr.pnl > 0]
        losses = tr[tr.pnl <= 0]
        out.update(
            win_rate=round(len(wins) / len(tr), 4),
            avg_r=round(tr.r.mean(), 3),
            avg_win_r=round(wins.r.mean(), 2) if len(wins) else None,
            avg_loss_r=round(losses.r.mean(), 2) if len(losses) else None,
            profit_factor=round(wins.pnl.sum() / -losses.pnl.sum(), 2) if losses.pnl.sum() < 0 else None,
            avg_days=round(tr.days.mean(), 1),
            trades_per_year=round(len(tr) / years, 1),
            best=tr.sort_values("r").iloc[-1][["symbol", "r"]].to_dict(),
        )
    return out


def curve(book: Book, bench: pd.Series, init_cad, step=1):
    pts = book.equity[::step]
    if book.equity and pts[-1] is not book.equity[-1]:
        pts.append(book.equity[-1])
    idx = pd.to_datetime([p[0] for p in pts])
    b = bench.reindex(bench.index.union(idx)).ffill().reindex(idx)
    b0 = b.iloc[0]
    return [[p[0], p[2], round(init_cad * bv / b0, 2)] for p, bv in zip(pts, b.values)]
