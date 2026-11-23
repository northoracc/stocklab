"""公開前處理：剔走止蝕設定紀錄，並把 today.json 轉成 today.csv（Google Sheet 用 IMPORTDATA 讀取）。

每個 AI 嘅 Sheet 都有一個「Today」分頁：=IMPORTDATA(".../data/today.csv")
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "site" / "data" / "today.json"
OUT = ROOT / "site" / "data" / "today.csv"

COLS = ["Type", "League", "AI", "Rank", "Action", "Ticker", "Shares", "Price", "Value",
        "ReturnPct", "Cash", "Note"]


HIST = ROOT / "site" / "data" / "arena" / "history.json"


def public(trades):
    """止蝕價屬於未觸發的指令，唔公開：剔走「設定止蝕」同「取消止蝕」嘅紀錄。"""
    return [t for t in trades if t.get("status") != "done"
            and not (t.get("status") == "rejected" and t.get("action") == "CANCEL")]


def main():
    d = json.loads(SRC.read_text(encoding="utf-8"))
    for L in d["leagues"].values():
        L["trades_today"] = public(L["trades_today"])
    SRC.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    if HIST.exists():
        h = json.loads(HIST.read_text(encoding="utf-8"))
        for L in h["leagues"].values():
            L["trades"] = public(L["trades"])
        HIST.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = [{"Type": "STATUS", "Note": f"status={d.get('status')} start={d.get('start')} "
                                       f"generated_at={d.get('generated_at')} {d.get('note', '')}"}]
    for lg, L in d["leagues"].items():
        rows.append({"Type": "BENCHMARK", "League": lg, "Ticker": L["benchmark"]["symbol"],
                     "ReturnPct": L["benchmark"]["return_pct"],
                     "Note": f"data_date={L.get('data_date')} currency={L['currency']}"})
        for s in L["standings"]:
            rows.append({"Type": "STANDING", "League": lg, "AI": s["ai"], "Rank": s["rank"],
                         "Value": s["equity"], "ReturnPct": s["return_pct"], "Cash": s["cash"],
                         "Note": f"day_change_pct={s['day_change_pct']}"})
            for h in s["holdings"]:
                rows.append({"Type": "HOLDING", "League": lg, "AI": s["ai"], "Ticker": h["ticker"],
                             "Shares": h["shares"], "Price": h["close"], "Value": h["value"],
                             "ReturnPct": h["pnl_pct"], "Note": f"avg_cost={h['avg_cost']}"})
        for t in L["trades_today"]:
            if t["status"] == "hold":
                continue
            rows.append({"Type": "TRADE", "League": lg, "AI": t["ai"], "Action": t["action"],
                         "Ticker": t["ticker"], "Shares": t["shares"], "Price": t["price"],
                         "Value": t["value"], "Note": f"{t['status']} {t['note']}".strip()})
    for o in d.get("overall", []):
        rows.append({"Type": "OVERALL", "AI": o["ai"], "Rank": o["rank"], "ReturnPct": o["avg_return_pct"]})
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"[today_csv] {len(rows)} 行 → {OUT}")


if __name__ == "__main__":
    main()
