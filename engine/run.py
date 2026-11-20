"""每日主程式：下載數據 → 回測 → 實盤模擬 → 輸出 site/data/*.json

用法：
  python engine/run.py               # 真數據（GitHub Actions 每日收市後跑）
  python engine/run.py --synthetic   # 隨機數據，離線測試用
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C  # noqa: E402
import data as D  # noqa: E402
import sim as S  # noqa: E402
import strategies as ST  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "site" / "data"


def jdump(obj, path):
    def default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, pd.Timestamp):
            return str(o.date())
        raise TypeError(type(o))
    path.write_text(json.dumps(obj, ensure_ascii=False, default=default, separators=(",", ":")),
                    encoding="utf-8")


def run_market(key, synthetic=False):
    m = C.MARKETS[key]
    syms = D.read_universe(m["universe_file"])
    bench_sym = m["benchmark"]
    if synthetic:
        panel = D.synthetic(syms + [bench_sym], C.DATA_START, seed=hash(key) % 1000)
        fx = D.synthetic_fx(C.DATA_START) if m["fx"] else None
    else:
        panel = D.fetch(syms + [bench_sym], C.DATA_START)
        fx = D.fetch_series(m["fx"], C.DATA_START) if m["fx"] else None
        if m["fx"] and fx is None:
            raise RuntimeError("下載唔到 USD/CAD 匯率")
    if bench_sym not in panel.symbols:
        raise RuntimeError(f"下載唔到基準 {bench_sym}")
    bench = panel.close[bench_sym].ffill()
    # 基準唔參與選股
    for f in D.FIELDS:
        setattr(panel, f, getattr(panel, f).drop(columns=[bench_sym]))

    ind = ST.Indicators(panel, bench)
    last = ind.close.index[-1]
    fxs = fx.reindex(ind.close.index).ffill().bfill() if fx is not None else None
    bench_cad = bench * fxs if fxs is not None else bench   # 基準都用 CAD 計，公平比較

    experts = []
    for sid, cfg in C.STRATEGIES.items():
        conds = ST.conditions(ind, cfg)
        sig = ST.signals(conds)
        sc = ST.score(ind, cfg)

        bt = S.run(ind, conds, sig, sc, cfg, C.BACKTEST_START, fxs, end=C.LIVE_START)
        bt_stats = S.stats(bt, bench_cad.loc[C.BACKTEST_START:C.LIVE_START], C.BACKTEST_START,
                           C.ACCOUNT["initial_cad"])
        bt_curve = S.curve(bt, bench_cad, C.ACCOUNT["initial_cad"], step=5)
        yearly = yearly_returns(bt, bench_cad)

        live = None
        if pd.Timestamp(C.LIVE_START) <= last:
            lb = S.run(ind, conds, sig, sc, cfg, C.LIVE_START, fxs)
            live = live_view(lb, ind, bench_cad, fxs, last)

        experts.append(dict(
            id=sid, name=cfg["name"], en=cfg["en"], tag=cfg["tag"], desc=cfg["desc"],
            rules=rule_text(cfg),
            today=dict(
                signals=[s for s in sig.loc[last][sig.loc[last]].index],
                near_misses=ST.explain_day(conds, sc, ind, last),
            ),
            backtest=dict(stats=bt_stats, curve=bt_curve, yearly=yearly,
                          recent_trades=bt.trades[-15:]),
            live=live,
        ))

    reg_close = float(bench.loc[last])
    reg_ma = float(ind.bench_ma200.loc[last])
    return dict(
        market=key, label=m["label"], currency=m["currency"], benchmark=bench_sym,
        data_date=str(last.date()), model_version=C.MODEL_VERSION,
        live_start=C.LIVE_START, backtest_start=C.BACKTEST_START,
        fx=round(float(fxs.loc[last]), 4) if fxs is not None else None,
        regime=dict(close=round(reg_close, 2), ma200=round(reg_ma, 2), ok=reg_close > reg_ma),
        universe=dict(total=len(syms), ok=len(panel.symbols), missing=panel.missing,
                      sources=sorted(set(panel.source.values()))),
        experts=experts,
    )


def yearly_returns(book, bench):
    eq = pd.Series([e[2] for e in book.equity], index=pd.to_datetime([e[0] for e in book.equity]))
    b = bench.reindex(eq.index).ffill()
    out = []
    prev_e, prev_b = eq.iloc[0], b.iloc[0]
    for y, grp in eq.groupby(eq.index.year):
        e1, b1 = grp.iloc[-1], b.loc[grp.index[-1]]
        out.append([int(y), round(e1 / prev_e - 1, 4), round(b1 / prev_b - 1, 4)])
        prev_e, prev_b = e1, b1
    return out


def live_view(book, ind, bench_cad, fxs, last):
    fx_now = float(fxs.loc[last]) if fxs is not None else 1.0
    positions = []
    for sym, p in book.positions.items():
        px = book.last_close.get(sym, p.entry)
        positions.append(dict(
            symbol=sym, shares=round(p.shares, 4), entry=round(p.entry, 2), last=round(px, 2),
            stop=round(p.stop, 2), entry_date=p.entry_date,
            pnl=round((px - p.entry) * p.shares, 2), pnl_pct=round(px / p.entry - 1, 4),
            r_now=round((px - p.entry) / p.risk_per_share, 2) if p.risk_per_share else None,
            value_cad=round(px * p.shares * fx_now, 2),
            exiting=sym in book.pending_sells,
        ))
    eq_cad = book.equity[-1][2]
    journal = [dict(date=d, events=evs) for d, evs in sorted(book.events.items(), reverse=True)][:40]
    return dict(
        equity_cad=eq_cad, return_pct=round(eq_cad / C.ACCOUNT["initial_cad"] - 1, 4),
        cash=round(book.cash, 2), cash_cad=round(book.cash * fx_now, 2),
        positions=positions,
        orders=dict(
            buys=[dict(symbol=o["symbol"], signal_close=round(o["signal_close"], 2),
                       half=not o["regime_ok"]) for o in book.pending_buys],
            sells=[dict(symbol=s, reason=r) for s, r in book.pending_sells.items()],
        ),
        trades=book.trades[::-1],
        stats=S.stats(book, bench_cad, C.LIVE_START, C.ACCOUNT["initial_cad"]),
        curve=S.curve(book, bench_cad, C.ACCOUNT["initial_cad"], step=1),
        journal=journal,
    )


def rule_text(cfg):
    r = []
    if cfg["base"]:
        b = cfg["base"]
        r.append(f"前 {b['days']} 日形成箱體，闊度 ≤ {b['atr_mult']:g}×ATR%（介乎 {b['min_pct']:.0%}–{b['max_pct']:.0%}）")
        r.append("收市突破箱頂")
        r.append(f"收市距 52 週高位 {cfg['near_high']:.0%} 以內")
    else:
        r.append(f"收市高於前 {cfg['breakout_days']} 日最高價")
    r.append(f"成交量 ≥ 前 {cfg['vol_avg_days']} 日均量 {cfg['vol_mult']:g} 倍")
    r.append({"ma20>ma50": "收市 > MA20 > MA50", "ma50>ma200": "收市 > MA50 > MA200",
              "close>ma200": "收市 > MA200"}[cfg["trend"]])
    r.append(f"{cfg['rs_days']} 日回報跑贏基準" + (f" {cfg['rs_min']:.0%} 以上" if cfg["rs_min"] else ""))
    ex = [f"追蹤止損：最高收市價 − {cfg['atr_stop']:g}×ATR"]
    if cfg.get("exit_ma"):
        ex.append(f"收市跌穿 MA{cfg['exit_ma']}")
    if cfg["time_stop_days"]:
        ex.append(f"持有 {cfg['time_stop_days']} 日仍未升 {cfg['time_stop_min_r']}R 就離場")
    return dict(entry=r, exit=ex, max_gap=cfg["max_gap"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--markets", default="us,ca")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    status = dict(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  model_version=C.MODEL_VERSION, synthetic=args.synthetic, markets={}, ok=True)
    for key in args.markets.split(","):
        try:
            rep = run_market(key, args.synthetic)
            rep["synthetic"] = args.synthetic
            jdump(rep, OUT / f"{key}.json")
            status["markets"][key] = dict(ok=True, data_date=rep["data_date"],
                                          missing=len(rep["universe"]["missing"]))
            print(f"[{key}] OK data_date={rep['data_date']}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            status["ok"] = False
            status["markets"][key] = dict(ok=False, error=str(e))
    status["settings"] = dict(account=C.ACCOUNT, costs=C.COSTS, regime=C.REGIME,
                              liquidity=C.LIQUIDITY)
    jdump(status, OUT / "status.json")
    if not status["ok"]:
        sys.exit(1)   # 令 GitHub Actions 標紅，會 email 通知你


if __name__ == "__main__":
    main()
