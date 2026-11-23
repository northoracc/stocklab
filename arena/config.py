"""Stocklab AI 選股比賽 — 規則參數。

改規則前請先喺設定指南（Google Doc）更新，再改呢度。
"""
import os

# 第一晚 AI 落單嘅日期（多倫多時間）。佢哋當晚寫嘅指令會喺下一個交易日成交。
START = os.environ.get("ARENA_START", "2026-11-23")

PARTICIPANTS = ["Claude", "ChatGPT", "Gemini", "Muse"]

LEAGUES = {
    "US": {"name": "美股聯賽", "currency": "USD", "capital": 5000.0, "benchmark": "^GSPC",
           "benchmark_name": "S&P 500"},
    "CA": {"name": "加股聯賽", "currency": "CAD", "capital": 5000.0, "benchmark": "^GSPTSE",
           "benchmark_name": "S&P/TSX 綜合指數"},
}

MAX_POSITIONS = 5          # 每個聯賽同一時間最多持有幾隻股票
ACTIONS = {"BUY", "SELL", "STOP", "CANCEL", "HOLD"}

# 收市後幾點先當日 K 線完整（多倫多時間）
CLOSE_READY = (16, 20)

# 每個參賽者嘅 CSV 來源：GitHub secret ARENA_SOURCES（JSON），
# 本機測試可以放 arena/sources.json（已 gitignore）。格式：
# {"Claude": {"orders": "<url>", "journal": "<url>"}, ...}
