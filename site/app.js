/* Stocklab v2 前端：讀 data/*.json，hash 路由 #view/market */
(function () {
  "use strict";
  const COLORS = { A: "var(--s1)", B: "var(--s2)", C: "var(--s3)", bench: "var(--bench)" };
  const HEX = { A: "#2a78d6", B: "#eb6834", C: "#1baf7a", bench: "#8a8984" };
  const VIEWS = ["journal", "backtest", "rules"];
  const cache = {};
  let status = null;

  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (v, d = 0) => v == null ? "—" : "$" + Number(v).toLocaleString("en-CA", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d = 1, sign = true) => v == null ? "—" : (sign && v > 0 ? "+" : "") + (v * 100).toFixed(d) + "%";
  const cls = (v) => v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
  const num = (v, d = 2) => v == null ? "—" : Number(v).toLocaleString("en-CA", { maximumFractionDigits: d });

  async function getJSON(path) {
    const r = await fetch(path + "?t=" + Date.now());
    if (!r.ok) throw new Error(path + " " + r.status);
    return r.json();
  }
  async function load(mkt) {
    if (!status) status = await getJSON("data/status.json").catch(() => null);
    if (!cache[mkt]) cache[mkt] = await getJSON(`data/${mkt}.json`);
    return cache[mkt];
  }

  function route() {
    const [v, m] = (location.hash.replace("#", "") || "journal/us").split("/");
    return { view: VIEWS.includes(v) ? v : "journal", mkt: m === "ca" ? "ca" : "us" };
  }

  async function render() {
    const { view, mkt } = route();
    document.querySelectorAll("#tabs a").forEach((a) => {
      a.classList.toggle("on", a.dataset.view === view);
      a.href = `#${a.dataset.view}/${mkt}`;
    });
    document.querySelectorAll("#mkt button").forEach((b) => b.classList.toggle("on", b.dataset.mkt === mkt));
    const main = $("#main");
    let r;
    try {
      r = await load(mkt);
    } catch (e) {
      $("#banner").innerHTML = `<div class="banner err">讀取唔到數據（${esc(e.message)}）。可能系統未完成第一次更新。</div>`;
      main.innerHTML = "";
      return;
    }
    banner(r, mkt);
    main.innerHTML = { journal, backtest, rules }[view](r);
    main.querySelectorAll("[data-chart]").forEach((el) => drawChart(el, JSON.parse(el.dataset.chart)));
    $("#foot-meta").textContent = `模型 v${r.model_version} · 數據日期 ${r.data_date}` + (status ? ` · 生成於 ${new Date(status.generated_at).toLocaleString("zh-HK", { timeZone: "America/Toronto" })}（多倫多時間）` : "");
    document.title = `${{ journal: "日報", backtest: "回測", rules: "規則" }[view]} · ${r.label}｜Stocklab`;
  }

  // ------------------------------------------------------------ 狀態提示
  function bizDaysBetween(a, b) {
    let n = 0; const d = new Date(a + "T12:00:00");
    const end = new Date(b + "T12:00:00");
    while (d < end) { d.setDate(d.getDate() + 1); if (d.getDay() % 6 !== 0) n++; }
    return n;
  }
  function banner(r, mkt) {
    const out = [];
    if (r.synthetic) out.push(`<div class="banner warn">⚠ 示範模式：目前顯示嘅係隨機生成嘅測試數據，唔係真實股價。GitHub Actions 第一次跑完之後會自動換成真數據。</div>`);
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/Toronto" });
    const lag = bizDaysBetween(r.data_date, today);
    if (lag > 2) out.push(`<div class="banner err">⚠ 數據最後日期係 ${r.data_date}，已經 ${lag} 個交易日冇更新。請檢查 GitHub Actions 有冇出錯。</div>`);
    const ms = status && status.markets && status.markets[mkt];
    if (ms && !ms.ok) out.push(`<div class="banner err">⚠ 最近一次更新失敗：${esc(ms.error)}（以下係上一次成功嘅數據）</div>`);
    if (r.universe.missing.length) out.push(`<div class="banner warn">有 ${r.universe.missing.length} 隻股票下載唔到數據，今日唔納入計算：${esc(r.universe.missing.join("、"))}</div>`);
    $("#banner").innerHTML = out.join("");
  }

  // ------------------------------------------------------------ 日報
  function headline(r) {
    const parts = r.experts.map((e) => {
      if (!e.live) return null;
      const b = e.live.orders.buys.map((o) => o.symbol), s = e.live.orders.sells.map((o) => o.symbol);
      const t = [];
      if (s.length) t.push("賣 " + s.join("、"));
      if (b.length) t.push("買 " + b.join("、"));
      return `${e.id} ${t.length ? t.join("，") : "不變"}`;
    }).filter(Boolean);
    return parts.length ? "明日：" + parts.join("；") + "。" : "";
  }

  function journal(r) {
    const live = r.experts.some((e) => e.live);
    const reg = r.regime;
    const chips = [
      `<span class="chip ${reg.ok ? "good" : "bad"}">${esc(r.benchmark)} <b>${num(reg.close)}</b> ${reg.ok ? "高於" : "低於"} MA200 ${num(reg.ma200)} · ${reg.ok ? "正常注碼" : "新倉注碼減半"}</span>`,
      r.fx ? `<span class="chip">USD/CAD <b>${r.fx.toFixed(4)}</b></span>` : "",
      `<span class="chip">候選池 <b>${r.universe.ok}</b> / ${r.universe.total} 隻</span>`,
    ].join("");
    let h = `<section class="hero">
      <div class="eyebrow">${esc(r.label)} · ${r.data_date} 收市後</div>
      <h1>${live ? esc(headline(r)) || "今日冇新指令。" : `實盤模擬將於 ${r.live_start} 開始`}</h1>
      <p class="sub">${live ? `由 ${r.live_start} 起，三位專家各以 CAD $3,000 獨立模擬；每日收市後決定，翌日開市執行。` : "開始之前，可以先睇「回測」頁，了解三套策略喺過去幾年嘅表現。下面係今日嘅掃描結果。"}</p>
      <div class="chips">${chips}</div></section>`;

    h += `<div class="grid3">${r.experts.map((e) => expertCard(e, r)).join("")}</div>`;

    if (live) {
      const e0 = r.experts.find((e) => e.live);
      if (e0.live.curve.length > 1) {
        h += `<h2>模擬淨值（CAD）</h2><div class="card">${chartHTML(r.experts, "live", r.benchmark)}</div>`;
      }
      h += `<h2>交易日誌</h2><div class="card">${timeline(r)}</div>`;
    }
    return h;
  }

  function expertCard(e, r) {
    const head = `<div class="ex-head"><span class="badge" style="background:${COLORS[e.id]}">${e.id}</span>
      <div><div class="nm">${esc(e.name)}</div><div class="en">${esc(e.en)} · ${esc(e.tag)}</div></div></div>`;
    let body = "";
    if (e.live) {
      const L = e.live, st = L.stats;
      body += `<div class="big">${money(L.equity_cad, 2)}</div>
        <div class="kv"><span>總回報</span><b class="${cls(L.return_pct)}">${pct(L.return_pct, 2)}</b></div>
        <div class="kv"><span>同期 ${esc(r.benchmark)}</span><b class="${cls(st.bench_return)}">${pct(st.bench_return, 2)}</b></div>
        <div class="kv"><span>已平倉交易</span><b>${st.trades}${st.trades ? ` · 勝率 ${pct(st.win_rate, 0, false)}` : ""}</b></div>
        <div class="kv"><span>現金</span><b>${money(L.cash_cad)} CAD</b></div>`;
      body += `<div class="sec"><div class="sec-t">持倉</div>${L.positions.length ? L.positions.map((p) => `
        <div class="row"><span><span class="sym">${esc(p.symbol)}</span> <span class="muted">${num(p.shares, 3)} 股 @ ${num(p.entry)}</span></span>
        <span><b class="${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</b> <span class="muted">止損 ${num(p.stop)}</span>${p.exiting ? ' <span class="tag sell">明日賣</span>' : ""}</span></div>`).join("") : '<div class="empty">全現金</div>'}</div>`;
      const o = L.orders;
      body += `<div class="sec"><div class="sec-t">明日開市指令</div>${o.buys.length + o.sells.length ? [
        ...o.sells.map((x) => `<div class="row"><span><span class="tag sell">賣</span> <span class="sym">${esc(x.symbol)}</span></span><span class="muted small">${esc(x.reason)}</span></div>`),
        ...o.buys.map((x) => `<div class="row"><span><span class="tag buy">買</span> <span class="sym">${esc(x.symbol)}</span></span><span class="muted small">開市價 ≤ ${num(x.signal_close * (1 + e.rules.max_gap))}${x.half ? " · 半注" : ""}</span></div>`),
      ].join("") : '<div class="empty">冇</div>'}</div>`;
    } else {
      const bs = e.backtest.stats;
      body += `<p class="small muted">${esc(e.desc)}</p>
        <div class="kv"><span>回測年化回報</span><b class="${cls(bs.cagr)}">${pct(bs.cagr)}</b></div>
        <div class="kv"><span>最大回撤</span><b class="neg">${pct(bs.max_drawdown, 1, false)}</b></div>
        <div class="kv"><span>每年交易</span><b>${bs.trades_per_year ?? "—"} 次</b></div>`;
    }
    const sig = e.today.signals;
    body += `<div class="sec"><div class="sec-t">今日合格訊號（${sig.length}）</div>${sig.length ? `<div class="small">${sig.map(esc).join("、")}</div>` : '<div class="empty">冇</div>'}</div>`;
    const nm = e.today.near_misses.filter((x) => x.failed.length).slice(0, 4);
    if (nm.length) body += `<div class="sec"><div class="sec-t">差一步</div>${nm.map((x) => `<div class="row"><span class="sym">${esc(x.symbol)}</span><span class="muted small">未過：${esc(x.failed.join("、"))}</span></div>`).join("")}</div>`;
    return `<div class="card">${head}${body}</div>`;
  }

  function evText(id, ev) {
    const b = (s) => `<b>${id} ${s}</b>`;
    switch (ev.type) {
      case "buy": return `${b("買入 " + esc(ev.symbol))} ${num(ev.shares, 3)} 股 @ ${num(ev.price)}，金額 ${num(ev.value)}，止損 ${num(ev.stop)}，計劃風險 ${num(ev.risk)}${ev.half ? "（大市弱，半注）" : ""}`;
      case "sell": return `${b("賣出 " + esc(ev.symbol))} @ ${num(ev.price)}，盈虧 <span class="${cls(ev.pnl)}">${num(ev.pnl)}（${ev.r > 0 ? "+" : ""}${ev.r}R）</span> · ${esc(ev.reason)}`;
      case "skip": return `${b("放棄 " + esc(ev.symbol))}：${esc(ev.reason)}`;
      case "signal": return `${b("訊號 " + esc(ev.symbol))} 收市 ${num(ev.close)}，排名第 ${ev.rank} → 明日開市買入`;
      case "exit_signal": return `${b("安排賣出 " + esc(ev.symbol))}：${esc(ev.reason)}`;
      case "signal_unfilled": return `<span class="muted">${id} 訊號 ${esc(ev.symbol)}：${esc(ev.reason)}</span>`;
      case "fx": return `<span class="muted">${id}：${esc(ev.text)}</span>`;
      default: return "";
    }
  }

  function timeline(r) {
    const byDate = {};
    r.experts.forEach((e) => (e.live?.journal || []).forEach((d) => {
      const list = (byDate[d.date] ||= []);
      list.push(...d.events.filter((ev) => ev.type !== "signal_unfilled").map((ev) => evText(e.id, ev)));
      const unf = d.events.filter((ev) => ev.type === "signal_unfilled").map((ev) => ev.symbol);
      if (unf.length) list.push(`<span class="muted">${e.id} 另有 ${unf.length} 個合格訊號因持倉已滿未採用：${esc(unf.join("、"))}</span>`);
    }));
    const dates = Object.keys(byDate).sort().reverse().slice(0, 20);
    if (!dates.length) return '<div class="empty">暫時未有紀錄</div>';
    return dates.map((d) => `<div class="tl-day"><div class="dt">${d}</div><div>${byDate[d].map((t) => `<div class="tl-ev">${t}</div>`).join("")}</div></div>`).join("");
  }

  // ------------------------------------------------------------ 回測
  function backtest(r) {
    const s0 = r.experts[0].backtest.stats;
    const rows = r.experts.map((e) => {
      const s = e.backtest.stats;
      return `<tr><td><span class="dot" style="background:${COLORS[e.id]}"></span>${e.id} · ${esc(e.name)}</td>
        <td class="${cls(s.cagr)}">${pct(s.cagr)}</td><td class="neg">${pct(s.max_drawdown, 1, false)}</td>
        <td>${money(s.final_cad)}</td><td>${s.trades}</td><td>${s.trades_per_year ?? "—"}</td>
        <td>${s.win_rate != null ? pct(s.win_rate, 0, false) : "—"}</td><td>${s.avg_r ?? "—"}</td>
        <td>${s.avg_win_r ?? "—"} / ${s.avg_loss_r ?? "—"}</td><td>${s.profit_factor ?? "—"}</td><td>${s.avg_days ?? "—"}</td></tr>`;
    }).join("");
    const benchFinal = 3000 * (1 + s0.bench_return);
    let h = `<section class="hero"><div class="eyebrow">${esc(r.label)} · 回測 ${s0.start} 至 ${s0.end}</div>
      <h1>三套策略嘅歷史表現</h1>
      <p class="sub">用同一套規則、同一套成本假設，喺歷史數據上逐日重播。起始本金各 CAD $3,000，基準為買入持有 ${esc(r.benchmark)}${r.fx ? "（已換算成 CAD）" : ""}。</p></section>
      <div class="card tscroll"><table>
      <thead><tr><th>策略</th><th>年化回報</th><th>最大回撤</th><th>期末淨值</th><th>交易數</th><th>每年交易</th><th>勝率</th><th>平均 R</th><th>贏 / 輸 R</th><th>盈虧比</th><th>平均持有日</th></tr></thead>
      <tbody>${rows}<tr><td><span class="dot" style="background:${COLORS.bench}"></span>${esc(r.benchmark)} 買入持有</td>
      <td class="${cls(s0.bench_cagr)}">${pct(s0.bench_cagr)}</td><td>—</td><td>${money(benchFinal)}</td><td colspan="7" class="muted" style="text-align:left">參考基準</td></tr></tbody></table></div>`;
    h += `<h2>淨值走勢（CAD）</h2><div class="card">${chartHTML(r.experts, "backtest", r.benchmark)}</div>`;
    const yrs = r.experts[0].backtest.yearly;
    h += `<h2>逐年回報</h2><div class="card tscroll"><table><thead><tr><th>年份</th>${r.experts.map((e) => `<th>${e.id}</th>`).join("")}<th>${esc(r.benchmark)}</th></tr></thead><tbody>${
      yrs.map((y, i) => `<tr><td>${y[0]}</td>${r.experts.map((e) => { const v = e.backtest.yearly[i]?.[1]; return `<td class="${cls(v)}">${pct(v)}</td>`; }).join("")}<td class="${cls(y[2])}">${pct(y[2])}</td></tr>`).join("")
    }</tbody></table></div>`;
    h += `<h2>最近嘅回測交易</h2>${r.experts.map((e) => `<details class="card" style="margin-bottom:10px"><summary><span class="dot" style="background:${COLORS[e.id]}"></span>${e.id} · ${esc(e.name)}（最近 ${e.backtest.recent_trades.length} 筆）</summary>
      <div class="tscroll"><table><thead><tr><th>股票</th><th>入場</th><th>出場</th><th>買入</th><th>賣出</th><th>盈虧</th><th>R</th><th>日數</th><th style="text-align:left">原因</th></tr></thead><tbody>${
        e.backtest.recent_trades.slice().reverse().map((t) => `<tr><td>${esc(t.symbol)}</td><td>${t.entry_date}</td><td>${t.exit_date}</td><td>${num(t.entry)}</td><td>${num(t.exit)}</td><td class="${cls(t.pnl)}">${num(t.pnl)}</td><td class="${cls(t.r)}">${t.r}</td><td>${t.days}</td><td style="text-align:left" class="muted">${esc(t.reason)}</td></tr>`).join("")
      }</tbody></table></div></details>`).join("")}`;
    h += `<h2>點樣睇回測</h2><div class="callout">
      <p><b>回測唔係保證。</b>候選池用嘅係今日嘅股票名單，過去已經下市或者跌出名單嘅股票冇計（倖存者偏差），所以回測結果通常會偏高。</p>
      <p>價格已按拆股同派息調整；成本按「規則」頁嘅假設計算，實際券商收費可能唔同。</p>
      <p>比較策略時，唔好淨係睇回報：最大回撤、每年交易次數同平均 R 一樣重要。平均 R 大過 0 先代表策略扣成本之後有正期望值。</p></div>`;
    return h;
  }

  // ------------------------------------------------------------ 規則
  function rules(r) {
    const st = status?.settings;
    const acc = st?.account, cost = st?.costs;
    let h = `<section class="hero"><div class="eyebrow">THE EXPERIMENT · v${r.model_version}</div>
      <h1>同一個理論，三種執行方式。</h1>
      <p class="sub">EOD 動能突破：先訂規則，再記錄結果。所有規則都寫死喺程式入面，唔會臨時改。</p></section>`;
    h += `<div class="grid3">${r.experts.map((e) => `<div class="card rules"><div class="ex-head"><span class="badge" style="background:${COLORS[e.id]}">${e.id}</span><div><div class="nm">${esc(e.name)}</div><div class="en">${esc(e.en)} · ${esc(e.tag)}</div></div></div>
      <p class="small muted">${esc(e.desc)}</p>
      <div class="sec-t">入場（全部要成立）</div><ul class="small">${e.rules.entry.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <div class="sec-t">執行</div><ul class="small"><li>翌日開市價高過訊號收市 ${pct(e.rules.max_gap, 0, false)} 以上就放棄（唔追高）</li></ul>
      <div class="sec-t">退出（任何一項）</div><ul class="small">${e.rules.exit.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")}</div>`;
    h += `<h2>三位共同遵守嘅資金規則</h2><div class="card rules small"><ul>
      <li>每位專家每個市場起始 CAD $${num(acc?.initial_cad ?? 3000, 0)}，最多 ${acc?.max_positions ?? 3} 隻股；冇合格訊號就保留現金。</li>
      <li>收市後決策，翌日開市執行。合格訊號多過空位時，按相對強度排名揀最強嗰幾隻。</li>
      <li>每筆計劃風險 = 當時淨值 ${pct(acc?.risk_per_trade_pct ?? 0.015, 1, false)}，按 ATR 止損距離計股數；單一部位最多佔淨值 ${pct(acc?.max_position_pct ?? 0.4, 0, false)}。</li>
      <li>${acc?.fractional === false ? "只准整股。" : "容許碎股，所以高價股（例如 COST、MELI）都買得到，注碼亦可以準確跟風險計。"}</li>
      <li>大市（${esc(r.benchmark)}）低於 MA200 時唔禁止入場，只係新倉注碼減半。</li>
      <li>冇固定獲利目標，由追蹤止損決定幾時走；止損只會向上移，唔會放寬。隔夜跳空可能令實際損失大過計劃風險。</li></ul></div>`;
    h += `<h2>成本假設</h2><div class="card rules small"><ul>
      <li>美股帳戶：開始時一次過將 CAD 換成 USD（成本 ${pct(cost?.fx_conversion_pct ?? 0.005, 1, false)}），之後一直用 USD 交易，<b>唔會每筆都換匯</b>。淨值按每日匯率換算成 CAD 顯示。</li>
      <li>佣金每次成交 ${cost?.commission ?? 1}（帳戶貨幣），滑價每邊 ${pct(cost?.slippage_pct ?? 0.001, 1, false)}。</li>
      <li>來回成本超過計劃風險 ${pct(cost?.max_cost_to_risk ?? 0.15, 0, false)} 嘅交易唔做，因為成本太高會食晒期望回報。</li>
      <li>稅項不計。</li></ul></div>`;
    h += `<h2>v2 改咗啲乜</h2><div class="callout"><ul>
      <li><b>入場條件放寬：</b>候選池擴大到 ${r.universe.total} 隻（v1 美股只得 38 隻）；箱體闊度按每隻股嘅波幅（ATR）計，唔再一刀切 15%；取消基本面門檻；大市弱時只係減半注碼，唔再全面禁止入場。</li>
      <li><b>成本大減：</b>唔再每筆交易都換匯（以前每次來回成本大約等於半個 R）。</li>
      <li><b>高價股買得到：</b>支援碎股。</li>
      <li><b>唔再錯過強勢突破：</b>改用開市價執行，跳空上限 3–4%，取代以前只容許 1% 嘅 stop-limit 單。</li>
      <li><b>唔會再卡死：</b>每日由起始日重新計算全部交易，某日冇跑到下次會自動補返；超過兩個交易日冇更新，網站會顯示警告。</li>
      <li><b>有歷史證據：</b>每套策略都有多年回測，唔使等幾個月先知有冇用。</li></ul></div>`;
    return h;
  }

  // ------------------------------------------------------------ 圖表
  function chartHTML(experts, kind, benchName) {
    const curves = experts.map((e) => ({ id: e.id, pts: kind === "live" ? e.live.curve : e.backtest.curve }));
    const dates = curves[0].pts.map((p) => p[0]);
    const series = curves.map((c) => ({ id: c.id, name: c.id, vals: c.pts.map((p) => p[1]) }));
    series.push({ id: "bench", name: benchName, vals: curves[0].pts.map((p) => p[2]), dash: true });
    const legend = series.map((s) => `<span><i style="background:${COLORS[s.id]}"></i>${esc(s.id === "bench" ? benchName + " 買入持有" : s.name + " · " + experts.find((e) => e.id === s.id).name)}</span>`).join("");
    return `<div class="legend">${legend}</div><div class="chart" data-chart='${esc(JSON.stringify({ dates, series }))}'></div>`;
  }

  function niceTicks(min, max, n = 5) {
    const span = max - min || 1, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
    const out = []; for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(v);
    return out;
  }

  function drawChart(el, { dates, series }) {
    const W = 1000, H = 340, m = { l: 64, r: 56, t: 10, b: 28 };
    const all = series.flatMap((s) => s.vals).filter((v) => v != null);
    let lo = Math.min(...all), hi = Math.max(...all);
    const pad = (hi - lo) * 0.06 || 50; lo -= pad; hi += pad;
    const ticks = niceTicks(lo, hi);
    const n = dates.length;
    const x = (i) => m.l + (n <= 1 ? 0 : (i / (n - 1)) * (W - m.l - m.r));
    const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="淨值走勢圖">`;
    ticks.forEach((t) => {
      svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(t)}" y2="${y(t)}" stroke="#efefea"/>`;
      svg += `<text x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end" font-size="12" fill="#8a8984">${money(t)}</text>`;
    });
    // x 軸：年份或月份
    const span = (new Date(dates[n - 1]) - new Date(dates[0])) / 864e5;
    let lastKey = "";
    dates.forEach((d, i) => {
      const key = span > 500 ? d.slice(0, 4) : d.slice(0, 7);
      if (key !== lastKey) {
        if (lastKey) svg += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle" font-size="12" fill="#8a8984">${span > 500 ? key : key.slice(5) + "月"}</text>`;
        lastKey = key;
      }
    });
    svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(3000)}" y2="${y(3000)}" stroke="#c9c8c2" stroke-dasharray="2 3"/>`;
    const labels = [];
    series.forEach((s) => {
      const d = s.vals.map((v, i) => (v == null ? "" : `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)).join("");
      svg += `<path d="${d}" fill="none" stroke="${HEX[s.id]}" stroke-width="2" ${s.dash ? 'stroke-dasharray="5 4"' : ""} stroke-linejoin="round"/>`;
      labels.push({ id: s.id, y: y(s.vals[n - 1]), text: s.id === "bench" ? "基準" : s.id });
    });
    // 直接標籤（避免重疊）
    labels.sort((a, b) => a.y - b.y);
    for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < 14) labels[i].y = labels[i - 1].y + 14;
    labels.forEach((l) => (svg += `<text x="${W - m.r + 8}" y="${l.y + 4}" font-size="12" font-weight="600" fill="#52514e">${l.text}</text>`));
    svg += `<line class="xh" y1="${m.t}" y2="${H - m.b}" stroke="#52514e" stroke-width="1" visibility="hidden"/>`;
    series.forEach((s) => (svg += `<circle class="pt" data-id="${s.id}" r="4.5" fill="${HEX[s.id]}" stroke="#fcfcfb" stroke-width="2" visibility="hidden"/>`));
    svg += `<rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent" class="hit"/></svg><div class="tip"></div>`;
    el.innerHTML = svg;
    const svgEl = el.querySelector("svg"), tip = el.querySelector(".tip"), xh = el.querySelector(".xh");
    const move = (ev) => {
      const pt = svgEl.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
      const p = pt.matrixTransform(svgEl.getScreenCTM().inverse());
      const i = Math.max(0, Math.min(n - 1, Math.round(((p.x - m.l) / (W - m.l - m.r)) * (n - 1))));
      xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("visibility", "visible");
      el.querySelectorAll(".pt").forEach((c) => {
        const s = series.find((q) => q.id === c.dataset.id), v = s.vals[i];
        c.setAttribute("cx", x(i)); c.setAttribute("cy", y(v)); c.setAttribute("visibility", v == null ? "hidden" : "visible");
      });
      tip.innerHTML = `<div class="d">${dates[i]}</div>` + series.slice().sort((a, b) => b.vals[i] - a.vals[i]).map((s) => `<div><span class="dot" style="background:${HEX[s.id]}"></span>${s.id === "bench" ? "基準" : s.id} <b>${money(s.vals[i])}</b> <span class="muted">${pct(s.vals[i] / 3000 - 1)}</span></div>`).join("");
      tip.style.display = "block";
      const rect = el.getBoundingClientRect(), px = (x(i) / W) * rect.width;
      tip.style.left = (px > rect.width * 0.6 ? px - tip.offsetWidth - 14 : px + 14) + "px";
      tip.style.top = "10px";
    };
    const leave = () => { tip.style.display = "none"; xh.setAttribute("visibility", "hidden"); el.querySelectorAll(".pt").forEach((c) => c.setAttribute("visibility", "hidden")); };
    svgEl.addEventListener("pointermove", move);
    svgEl.addEventListener("pointerleave", leave);
  }

  // ------------------------------------------------------------ 啟動
  document.querySelectorAll("#mkt button").forEach((b) => b.addEventListener("click", () => {
    location.hash = `${route().view}/${b.dataset.mkt}`;
  }));
  window.addEventListener("hashchange", render);
  render();
})();
