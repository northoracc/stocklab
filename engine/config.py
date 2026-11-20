"""Stocklab v2 — 所有規則參數集中喺呢度。改規則 = 改呢個檔 + 升 MODEL_VERSION。"""

MODEL_VERSION = "2.0.0"

# 實盤模擬由呢日開始（之前嘅日子只用嚟做回測）。改規則時請將呢個日期改做新版本生效日。
import os
LIVE_START = os.environ.get("STOCKLAB_LIVE_START", "2026-11-20")
# 回測起點
BACKTEST_START = "2018-01-02"
# 下載數據由幾時開始（要比回測早至少一年，計 MA200 / 52 週高位）
DATA_START = "2016-06-01"

ACCOUNT = {
    "initial_cad": 3000.0,        # 每位專家每個市場起始本金
    "max_positions": 3,
    "risk_per_trade_pct": 0.015,  # 每筆計劃風險 = 淨值 1.5%
    "max_position_pct": 0.40,     # 單一部位最多佔淨值 40%
    "fractional": True,           # 容許碎股（Wealthsimple 等券商支援）；改 False 就只准整股
    "min_order_value": 50.0,      # 單筆最少落單金額（帳戶貨幣）
}

COSTS = {
    # 美股帳戶：開始時一次過換匯成 USD，之後一直用 USD 現金交易，唔會每筆都換
    "fx_conversion_pct": 0.005,       # 只喺開戶時收一次
    "commission": 1.0,                # 每次成交（帳戶貨幣）；用零佣券商可改 0
    "slippage_pct": 0.001,            # 每邊滑價
    "max_cost_to_risk": 0.15,         # 來回成本 > 計劃風險 15% 就唔做呢單
}

REGIME = {
    # 大市 < MA200：唔禁止入場，只係注碼減半
    "below_ma200_size_mult": 0.5,
}

STRATEGIES = {
    "A": {
        "name": "快速突破型", "en": "Aggressive", "tag": "Early Momentum Breakout",
        "desc": "20 日新高 + 量能，及早入場；追蹤止損較近，接受較多假突破。",
        "breakout_days": 20, "vol_mult": 1.2, "vol_avg_days": 20,
        "trend": "ma20>ma50", "rs_days": 63, "rs_min": 0.0,
        "atr_stop": 2.5, "max_gap": 0.04,
        "time_stop_days": 15, "time_stop_min_r": 0.5,
        "base": None, "near_high": None,
    },
    "B": {
        "name": "標準動能突破型", "en": "Baseline", "tag": "Confirmed Momentum Breakout",
        "desc": "50 日新高、MA50 高於 MA200、相對強度排前列，確認升勢先入。",
        "breakout_days": 50, "vol_mult": 1.3, "vol_avg_days": 20,
        "trend": "ma50>ma200", "rs_days": 126, "rs_min": 0.05,
        "atr_stop": 3.0, "max_gap": 0.03,
        "time_stop_days": None, "time_stop_min_r": None,
        "base": None, "near_high": None,
    },
    "C": {
        "name": "整固箱體突破型", "en": "Patient", "tag": "Volatility-Adjusted Base Breakout",
        "desc": "等股價喺高位附近整固（箱體闊度按波幅計），突破箱頂先入，盡量持有長升浪。",
        "breakout_days": None, "vol_mult": 1.5, "vol_avg_days": 50,
        "trend": "close>ma200", "rs_days": 126, "rs_min": 0.0,
        "atr_stop": 4.0, "max_gap": 0.03,
        "time_stop_days": None, "time_stop_min_r": None,
        # 箱體：前 base_days 日嘅 (高-低)/高 ≤ max(base_min_pct, base_atr_mult × ATR%)
        "base": {"days": 35, "atr_mult": 6.0, "min_pct": 0.10, "max_pct": 0.30},
        "near_high": 0.25,   # 收市價距 52 週高位 25% 以內
        "exit_ma": 50,       # 收市跌穿 MA50 亦退出
    },
}

LIQUIDITY = {"min_price": 5.0, "min_dollar_vol": 10_000_000}  # 20 日平均成交額（當地貨幣）

MARKETS = {
    "us": {
        "label": "美股", "currency": "USD", "benchmark": "SPY", "fx": "CAD=X",
        "universe_file": "universe_us.txt",
    },
    "ca": {
        "label": "加拿大", "currency": "CAD", "benchmark": "XIU.TO", "fx": None,
        "universe_file": "universe_ca.txt",
    },
}
