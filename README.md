# Stocklab v2：三專家投資日誌

三套動能突破策略（A 快速／B 標準／C 整固箱體），美股同加股每日收市後模擬決策，翌日開市執行，並記錄結果。另附 2018 年起嘅歷史回測。

**全部免費運作**：GitHub Actions 每日自動跑，股價數據用 yfinance（免費、唔使 API key），網站放 Cloudflare Pages。唔需要任何 AI API credit。

```
engine/            Python 策略引擎
  config.py        ← 所有規則參數（改規則就改呢度）
  universe_us.txt  ← 美股候選池（每行可放幾隻，自由增減）
  universe_ca.txt  ← 加股候選池
  data.py          數據下載（yfinance，Stooq 後備）
  strategies.py    指標同入場條件
  sim.py           組合模擬器（收市決策、翌日開市執行、成本）
  run.py           主程式 → 輸出 site/data/*.json
site/              靜態網站（Cloudflare Pages 直接發佈呢個資料夾）
.github/workflows/daily.yml   每日自動更新
```

---

## 一次性設定（大約 15 分鐘）

### 1. 放上 GitHub
1. 去 github.com → **New repository**，名例如 `stocklab`。**揀 Public**，咁 GitHub Actions 就係完全免費、冇分鐘數限制。如果揀 Private，每月有 2,000 分鐘免費額度，呢個項目每月大約用 100 分鐘，都夠用。
2. 將呢個資料夾嘅所有檔案上傳到 repo。可以直接喺網頁 **Add file → Upload files** 拖入去；記得連 `.github` 資料夾都要上傳。
3. 去 repo 嘅 **Settings → Actions → General → Workflow permissions**，揀 **Read and write permissions**，然後撳 Save。

### 2. 第一次手動跑
1. 去 repo 嘅 **Actions** 分頁 → 揀 **daily-update** → **Run workflow**。
2. 大約 3–5 分鐘後會見到綠色剔號，`site/data/` 入面嘅示範數據就會換成真數據。
3. 之後每個交易日多倫多時間大約 17:30（冬令）／18:30（夏令）會自動再跑。

### 3. 接駁 Cloudflare Pages
1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**，揀返個 `stocklab` repo。
2. Build 設定：
   - Framework preset：**None**
   - Build command：**留空**
   - Build output directory：**`site`**
3. 撳 **Save and Deploy**。之後每次 GitHub Actions push 新數據，Cloudflare 都會自動重新發佈。
4. **Custom domains → Set up a custom domain** → 輸入 `stocklab.northora.cc`。如果之前已經有一條 DNS record 指向舊網站（chatgpt.site），要先去 DNS 頁面刪咗或者改咗佢，再加 custom domain。

---

## 日常運作

- **唔使理佢**：每日自動更新。
- **更新失敗**：GitHub 會 email 通知你（Actions 標紅），網站頂部亦會出現紅色提示。超過兩個交易日冇新數據，網站都會顯示警告。
- **某日冇跑到**：唔使處理。每次運行都會由 `LIVE_START` 重新計算全部交易，下次跑就會自動補返。

## 改規則

1. 改 `engine/config.py` 入面嘅參數。
2. 將 `MODEL_VERSION` 升一級（例如 2.0.0 → 2.1.0）。
3. **將 `LIVE_START` 改做新規則生效嘅日期**。因為每次都係由 `LIVE_START` 重新計算，如果唔改，舊日子嘅交易會用新規則重寫，實盤紀錄就唔再誠實。
4. 上傳之後，喺 Actions 手動 Run 一次。

常改嘅參數：

| 參數 | 意思 |
|---|---|
| `ACCOUNT.fractional` | `True` 容許碎股；你券商唔支援就改 `False` |
| `COSTS.commission` | 每次成交佣金；用零佣券商（例如 Wealthsimple）可以改 `0` |
| `ACCOUNT.risk_per_trade_pct` | 每筆風險佔淨值比例（預設 1.5%） |
| `STRATEGIES.X.max_gap` | 開市跳空超過幾多就放棄唔買 |

## 喺自己電腦試行

```bash
pip install -r requirements.txt
python engine/run.py --synthetic      # 隨機數據，唔使上網
python engine/run.py                  # 真數據
cd site && python -m http.server 8000 # 然後開 http://localhost:8000
```

想睇實盤畫面點樣運作，可以假設由較早日子開始：
`STOCKLAB_LIVE_START=2026-06-01 python engine/run.py`

## 已知限制

- **yfinance 係非官方工具**：Yahoo 偶然會改格式或者限流。遇到嘅話通常更新 yfinance 版本就得，`pip install -U yfinance`；美股會自動試 Stooq 做後備。
- **倖存者偏差**：回測用嘅係今日嘅候選名單，已經下市嘅股票冇計，所以回測結果會偏高。
- **價格經調整**：模擬用拆股同派息調整後價格，同券商實際成交價會有少少出入。
- 呢個係模擬研究，唔構成投資建議。
