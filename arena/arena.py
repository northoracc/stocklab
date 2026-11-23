"""Stocklab AI 選股比賽 — 每日計分。

流程：讀取每個參賽者 Google Sheet（已發佈 CSV）嘅 Orders 同 Journal
→ 用 yfinance 抓 Yahoo 價格 → 由開賽日起重新計算全部成交
→ 輸出 site/data/today.json 同 site/data/arena/history.json。

已經計過嘅交易日，指令會凍結喺 arena/ledger.json：之後 Sheet 再改或者刪行，
都唔會改寫歷史。

用法：
  python arena/arena.py               # 真數據
  python arena/arena.py --synthetic   # 隨機價格，離線測試
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import config as C  # noqa: E402

TZ = ZoneInfo("America/Toronto")
OUT = ROOT / "site" / "data"
LEDGER = HERE / "ledger.json"
FIELDS = ["Open", "High", "Low", "Close", "Stock Splits"]


# ------------------------------------------------------------------ 讀取 Sheet
def load_sources() -> dict:
    raw = os.environ.get("ARENA_SOURCES", "").strip()
    if raw:
        return json.loads(raw)
    p = HERE / "sources.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    sys.exit("找不到 ARENA_SOURCES（GitHub secret）或 arena/sources.json")


def fetch_text(src: str) -> str:
    if not src.startswith("http"):
        return (HERE / src).read_text(encoding="utf-8") if not Path(src).is_absolute() \
            else Path(src).read_text(encoding="utf-8")
    err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "stocklab-arena"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8-sig")
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"讀取失敗：{src} ({err})")


def read_rows(src: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(fetch_text(src))))
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rows]


def parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def num(s) -> float | None:
    s = re.sub(r"[,$\s]", "", str(s or ""))
    if not s:
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except ValueError:
        return None


def clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def normalize_order(ai: str, row: dict) -> dict | None:
    if not any(row.values()):
        return None
    d = parse_date(row.get("Date", ""))
    return {
        "ai": ai,
        "date": d.isoformat() if d else row.get("Date", ""),
        "league": row.get("League", "").upper(),
        "action": row.get("Action", "").upper(),
        "ticker": row.get("Ticker", "").upper().replace(" ", ""),
        "shares": num(row.get("Shares")),
        "stop": num(row.get("StopPrice")),
        "price_seen": clip(row.get("PriceSeen", ""), 60),
        "reason": clip(row.get("Reason", ""), 300),
        "sources": clip(row.get("Sources", ""), 300),
    }


def normalize_journal(ai: str, row: dict) -> dict | None:
    if not any(row.values()):
        return None
    d = parse_date(row.get("Date", ""))
    if not d:
        return None
    return {
        "ai": ai, "date": d.isoformat(), "league": row.get("League", "").upper(),
        "market_view": clip(row.get("MarketView", ""), 600),
        "analysis": clip(row.get("Analysis", ""), 600),
        "decision": clip(row.get("Decision", ""), 600),
        "opponent_impact": clip(row.get("OpponentImpact", ""), 600),
    }


def order_key(o: dict) -> str:
    return "|".join(str(o.get(k)) for k in ("ai", "date", "league", "action", "ticker", "shares", "stop"))


US_RE = re.compile(r"^[A-Z][A-Z0-9\-]{0,9}$")
CA_RE = re.compile(r"^[A-Z][A-Z0-9\-]{0,9}\.TO$")


def validate(o: dict) -> str | None:
    if not parse_date(o["date"]):
        return "日期無法解讀"
    if o["league"] not in C.LEAGUES:
        return "League 必須是 US 或 CA"
    a = o["action"]
    if a not in C.ACTIONS:
        return "Action 無效"
    if a == "HOLD":
        return None
    t = o["ticker"]
    if not t:
        return "缺少股票代號"
    if o["league"] == "US" and not US_RE.match(t):
        return "美股代號格式錯誤（不可帶 .TO / .V 等後綴）"
    if o["league"] == "CA" and not CA_RE.match(t):
        return "加股代號必須以 .TO 結尾（不准 .V）"
    if a in ("BUY", "SELL"):
        s = o["shares"]
        if s is None or s <= 0 or abs(s - round(s)) > 1e-9:
            return "股數必須是正整數"
    if a == "STOP" and (o["stop"] is None or o["stop"] <= 0):
        return "缺少有效止蝕價"
    return None


# ------------------------------------------------------------------ 價格
def _frame(df: pd.DataFrame, field: str, tickers: list[str]) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        if field not in df.columns.get_level_values(0):
            return pd.DataFrame(index=df.index)
        part = df[field]
    else:
        part = df[[field]].rename(columns={field: tickers[0]}) if field in df.columns \
            else pd.DataFrame(index=df.index)
    if isinstance(part, pd.Series):
        part = part.to_frame(tickers[0])
    return part


def download_prices(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    tickers = sorted(set(tickers))
    err = None
    for attempt in range(3):
        try:
            df = yf.download(tickers, start=start, auto_adjust=False, actions=True,
                             progress=False, group_by="column", threads=True)
            if df is not None and not df.empty:
                out = {}
                for f in FIELDS:
                    part = _frame(df, f, tickers)
                    part.index = pd.to_datetime(part.index).date
                    out[f] = part
                return out
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"yfinance 下載失敗：{err}")


def synthetic_prices(tickers: list[str], start: str, seed: int = 7) -> dict[str, pd.DataFrame]:
    import numpy as np
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(pd.Timestamp(start), pd.Timestamp(datetime.now(TZ).date())).date
    out = {f: pd.DataFrame(index=days) for f in FIELDS}
    for t in sorted(set(tickers)):
        p = 100.0 if not t.startswith("^") else 5000.0
        o, h, lo, c = [], [], [], []
        for _ in days:
            op = p * (1 + rng.normal(0, 0.006))
            cl = op * (1 + rng.normal(0, 0.012))
            o.append(round(op, 2)); c.append(round(cl, 2))
            h.append(round(max(op, cl) * (1 + abs(rng.normal(0, 0.006))), 2))
            lo.append(round(min(op, cl) * (1 - abs(rng.normal(0, 0.006))), 2))
            p = cl
        out["Open"][t], out["High"][t], out["Low"][t], out["Close"][t] = o, h, lo, c
        out["Stock Splits"][t] = 0.0
    return out


def px(prices, field, day, ticker):
    df = prices[field]
    if ticker not in df.columns or day not in df.index:
        return None
    v = df.at[day, ticker]
    return None if v is None or pd.isna(v) or v <= 0 else float(v)


# ------------------------------------------------------------------ 模擬
def r2(x):
    return round(float(x), 2)


class Account:
    def __init__(self, capital: float):
        self.cash = capital
        self.pos: dict[str, dict] = {}     # ticker -> {shares, cost}
        self.stops: dict[str, float] = {}
        self.last_close: dict[str, float] = {}

    def sell_all(self, t, price):
        p = self.pos.pop(t)
        self.cash += p["shares"] * price
        self.stops.pop(t, None)
        return p

    def equity(self):
        return self.cash + sum(p["shares"] * self.last_close.get(t, p["cost"] / p["shares"])
                               for t, p in self.pos.items())


def run_day(acc: Account, orders: list[dict], prices, day) -> list[dict]:
    trades = []

    def rec(o, status, note="", shares=0, price=None, action=None):
        trades.append({
            "date": day.isoformat(), "ai": o["ai"], "league": o["league"],
            "action": action or o["action"], "ticker": o.get("ticker", ""),
            "shares": int(shares), "price": r2(price) if price else None,
            "value": r2(shares * price) if price else None,
            "status": status, "note": note, "order_date": o.get("date", ""),
            "reason": o.get("reason", ""), "sources": o.get("sources", ""),
        })

    # 拆股
    for t in list(acc.pos):
        ratio = px(prices, "Stock Splits", day, t)
        if ratio and ratio > 0 and ratio != 1:
            acc.pos[t]["shares"] = int(round(acc.pos[t]["shares"] * ratio))
            if t in acc.stops:
                acc.stops[t] /= ratio

    valid, bad = [], []
    for o in orders:
        e = validate(o)
        (bad if e else valid).append((o, e))
    for o, e in bad:
        rec(o, "rejected", e)

    by = {a: [o for o, _ in valid if o["action"] == a] for a in C.ACTIONS}
    for o in by["HOLD"]:
        rec(o, "hold")
    for o in by["CANCEL"]:
        had = acc.stops.pop(o["ticker"], None)
        rec(o, "done" if had else "rejected", "已取消止蝕" if had else "沒有可取消的止蝕單")
    for o in by["STOP"]:
        acc.stops[o["ticker"]] = o["stop"]
        rec(o, "done", f"止蝕價設於 {o['stop']:g}")

    # 開市：跳空止蝕
    for t in list(acc.pos):
        stop, op = acc.stops.get(t), px(prices, "Open", day, t)
        if stop and op and op <= stop:
            sh = acc.pos[t]["shares"]
            acc.sell_all(t, op)
            rec({"ai": orders[0]["ai"] if orders else "", "league": "", "ticker": t},
                "filled", "止蝕：開市已跌穿止蝕價，以開市價成交", sh, op, "STOP")
    # 開市：賣出
    for o in by["SELL"]:
        t, op = o["ticker"], px(prices, "Open", day, o["ticker"])
        if t not in acc.pos:
            rec(o, "rejected", "未持有此股票")
            continue
        if not op:
            rec(o, "rejected", "Yahoo 當日沒有此股票開市價")
            continue
        held = acc.pos[t]["shares"]
        q = min(int(round(o["shares"])), held)
        if q == held:
            acc.sell_all(t, op)
        else:
            acc.pos[t]["cost"] *= (held - q) / held
            acc.pos[t]["shares"] = held - q
            acc.cash += q * op
        rec(o, "filled", "" if q == int(round(o["shares"])) else f"只持有 {held} 股，全數賣出", q, op)
    # 開市：買入
    for o in by["BUY"]:
        t, op = o["ticker"], px(prices, "Open", day, o["ticker"])
        if not op:
            rec(o, "rejected", "Yahoo 當日沒有此股票開市價（代號可能錯誤）")
            continue
        if t not in acc.pos and len(acc.pos) >= C.MAX_POSITIONS:
            rec(o, "rejected", f"已持有 {C.MAX_POSITIONS} 隻股票")
            continue
        want = int(round(o["shares"]))
        q = min(want, int(acc.cash // op))
        if q < 1:
            rec(o, "rejected", "現金不足")
            continue
        acc.cash -= q * op
        p = acc.pos.setdefault(t, {"shares": 0, "cost": 0.0})
        p["shares"] += q
        p["cost"] += q * op
        rec(o, "filled", "" if q == want else f"現金不足，只買入 {q} 股", q, op)
    # 盤中：止蝕
    for t in list(acc.pos):
        stop = acc.stops.get(t)
        lo, op = px(prices, "Low", day, t), px(prices, "Open", day, t)
        if stop and lo and lo <= stop:
            fill = min(stop, op) if op else stop
            sh = acc.pos[t]["shares"]
            acc.sell_all(t, fill)
            rec({"ai": orders[0]["ai"] if orders else "", "league": "", "ticker": t},
                "filled", "止蝕：盤中觸及止蝕價", sh, fill, "STOP")
    # 沒有持倉的止蝕單作廢
    for t in list(acc.stops):
        if t not in acc.pos:
            acc.stops.pop(t)
    # 收市估值
    for t in acc.pos:
        c = px(prices, "Close", day, t)
        if c:
            acc.last_close[t] = c
    return trades


# ------------------------------------------------------------------ 主程式
def completed_days(prices, benchmark) -> list[date]:
    now = datetime.now(TZ)
    today = now.date()
    ready = (now.hour, now.minute) >= C.CLOSE_READY
    close = prices["Close"]
    if benchmark not in close.columns:
        raise RuntimeError(f"沒有基準指數 {benchmark} 的數據")
    days = [d for d, v in close[benchmark].items() if pd.notna(v)]
    return sorted(d for d in days if d < today or (d == today and ready))


def exec_day(order_date: str, cal: list[date]) -> date | None:
    d = parse_date(order_date)
    if not d:
        return None
    for t in cal:
        if t > d:
            return t
    return None


def jdump(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    start = parse_date(C.START)
    sources = load_sources()
    orders, journals = [], []
    for ai in C.PARTICIPANTS:
        if ai not in sources:
            raise SystemExit(f"ARENA_SOURCES 缺少 {ai}")
        for r in read_rows(sources[ai]["orders"]):
            o = normalize_order(ai, r)
            if o:
                orders.append(o)
        for r in read_rows(sources[ai]["journal"]):
            j = normalize_journal(ai, r)
            if j:
                journals.append(j)
    print(f"[arena] 讀入 {len(orders)} 個指令、{len(journals)} 篇日記")

    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    ledger.setdefault("start", C.START)
    if ledger["start"] != C.START:
        print("[arena] 開賽日期已改變，重設 ledger")
        ledger = {"start": C.START}
    ledger.setdefault("frozen", {lg: {} for lg in C.LEAGUES})

    price_start = (pd.Timestamp(start) - pd.Timedelta(days=10)).date().isoformat()
    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "start": C.START, "synthetic": args.synthetic, "leagues": {}}
    history = {"start": C.START, "leagues": {}, "journals": []}
    late = []

    for lg, L in C.LEAGUES.items():
        frozen = ledger["frozen"].setdefault(lg, {})
        frozen_tickers = {o["ticker"] for day in frozen.values() for o in day if o.get("ticker")}
        tickers = {o["ticker"] for o in orders if o["league"] == lg and o["ticker"]} | frozen_tickers
        tickers = {t for t in tickers if (US_RE if lg == "US" else CA_RE).match(t)}
        tickers.add(L["benchmark"])
        prices = synthetic_prices(list(tickers), price_start) if args.synthetic \
            else download_prices(list(tickers), price_start)
        cal_all = completed_days(prices, L["benchmark"])
        base_days = [d for d in cal_all if d <= start]
        cal = [d for d in cal_all if d > start]

        # 凍結新交易日嘅指令
        lg_orders = [o for o in orders if o["league"] == lg or (o["league"] not in C.LEAGUES and lg == "US")]
        for t in cal:
            key = t.isoformat()
            todays = [o for o in lg_orders if exec_day(o["date"], cal_all) == t
                      and (parse_date(o["date"]) or start) >= start]
            if key not in frozen:
                frozen[key] = todays
            else:
                known = {order_key(o) for o in frozen[key]}
                late += [o for o in todays if order_key(o) not in known]

        accs = {ai: Account(L["capital"]) for ai in C.PARTICIPANTS}
        curves = {ai: [] for ai in C.PARTICIPANTS}
        all_trades = []
        bench_base = None
        if base_days:
            bench_base = px(prices, "Close", base_days[-1], L["benchmark"])
        bench_curve = []
        for t in cal:
            day_orders = frozen.get(t.isoformat(), [])
            for ai in C.PARTICIPANTS:
                mine = [o for o in day_orders if o["ai"] == ai]
                for tr in run_day(accs[ai], mine, prices, t):
                    tr["ai"], tr["league"] = ai, lg
                    all_trades.append(tr)
                curves[ai].append({"date": t.isoformat(), "equity": r2(accs[ai].equity())})
            bc = px(prices, "Close", t, L["benchmark"])
            if bench_base is None:
                bench_base = px(prices, "Open", t, L["benchmark"]) or bc
            if bc and bench_base:
                bench_curve.append({"date": t.isoformat(), "return_pct": r2((bc / bench_base - 1) * 100)})

        data_date = cal[-1].isoformat() if cal else None
        standings = []
        for ai in C.PARTICIPANTS:
            a = accs[ai]
            eq = a.equity()
            prev = curves[ai][-2]["equity"] if len(curves[ai]) > 1 else L["capital"]
            hold = []
            for tk, p in sorted(a.pos.items()):
                c = a.last_close.get(tk, p["cost"] / p["shares"])
                avg = p["cost"] / p["shares"]
                hold.append({"ticker": tk, "shares": p["shares"], "avg_cost": r2(avg), "close": r2(c),
                             "value": r2(p["shares"] * c), "pnl_pct": r2((c / avg - 1) * 100)})
            standings.append({"ai": ai, "equity": r2(eq), "cash": r2(a.cash),
                              "return_pct": r2((eq / L["capital"] - 1) * 100),
                              "day_change_pct": r2((eq / prev - 1) * 100) if prev else 0.0,
                              "holdings": hold})
        standings.sort(key=lambda s: -s["equity"])
        for i, s in enumerate(standings, 1):
            s["rank"] = i

        result["leagues"][lg] = {
            "name": L["name"], "currency": L["currency"], "capital": L["capital"],
            "data_date": data_date,
            "benchmark": {"symbol": L["benchmark"], "name": L["benchmark_name"],
                          "return_pct": bench_curve[-1]["return_pct"] if bench_curve else 0.0},
            "standings": standings,
            "trades_today": [tr for tr in all_trades if tr["date"] == data_date],
        }
        history["leagues"][lg] = {"curves": curves, "benchmark": bench_curve, "trades": all_trades}

        # 日記：指令成交後先公開
        for j in journals:
            if j["league"] != lg:
                continue
            ex = exec_day(j["date"], cal_all)
            if ex and ex in cal:
                history["journals"].append({**j, "published": ex.isoformat()})

    # 總排名
    overall = []
    for ai in C.PARTICIPANTS:
        rets = {lg: next(s["return_pct"] for s in result["leagues"][lg]["standings"] if s["ai"] == ai)
                for lg in C.LEAGUES}
        overall.append({"ai": ai, **{f"{lg.lower()}_return_pct": v for lg, v in rets.items()},
                        "avg_return_pct": r2(sum(rets.values()) / len(rets))})
    overall.sort(key=lambda x: -x["avg_return_pct"])
    for i, o in enumerate(overall, 1):
        o["rank"] = i
    result["overall"] = overall

    dates = [L["data_date"] for L in result["leagues"].values() if L["data_date"]]
    latest = max(dates) if dates else None
    result["status"] = "live" if latest else "pre-start"
    history["journals"].sort(key=lambda j: (j["published"], j["league"], j["ai"]))
    result["journals_today"] = [j for j in history["journals"] if j["published"] == latest]
    result["note"] = ("只包含已成交的交易；未成交的指令不會公開。日記在相關指令成交後才公開。"
                      if latest else f"比賽尚未開始。第一晚落單日期：{C.START}。")
    if late:
        result["late_orders_ignored"] = len(late)
        print(f"[arena] {len(late)} 個遲交指令被忽略")

    jdump(result, OUT / "today.json")
    jdump(history, OUT / "arena" / "history.json")
    jdump(ledger, LEDGER)
    print(f"[arena] 完成：status={result['status']} data_date={latest}")


if __name__ == "__main__":
    main()
