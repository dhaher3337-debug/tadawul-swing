# -*- coding: utf-8 -*-
"""
بناء تقرير HTML احترافي — V9
=====================================
محتويات جديدة:
  - نتائج ML (AUC, precision, recall, feature importance)
  - تدفق السيولة القطاعي
  - فرص Catch-up (من قائد إلى متأخر)
  - دوران القطاعات المكتشف
  - Expected Value لكل سهم
  - ألوان مُحسنة + layout نظيف
"""
import json
from datetime import datetime
from pathlib import Path

BASE = Path("tadawul_data")
OUT = Path("public")
try:
    if not OUT.exists():
        OUT.mkdir(parents=True, exist_ok=True)
except FileExistsError:
    pass


def load_json(p, d=None):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d if d is not None else {}


CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',Tahoma,'Arabic UI Text',sans-serif;background:#020408;color:#c9d6df;line-height:1.6;direction:rtl}
.wrap{max-width:1100px;margin:0 auto;padding:16px}
header{text-align:center;padding:24px 0;border-bottom:1px solid rgba(52,211,153,.15)}
header h1{font-size:24px;font-weight:800;background:linear-gradient(135deg,#34d399,#22d3ee,#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
header .sub{font-size:12px;color:#64748b;margin-top:4px}
.outlook{margin:16px 0;padding:14px;border-radius:10px;text-align:center;font-size:15px;font-weight:700}
.outlook.صاعد{background:rgba(52,211,153,.1);border:1px solid rgba(52,211,153,.3);color:#34d399}
.outlook.هابط{background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.3);color:#f87171}
.outlook.محايد{background:rgba(251,191,36,.1);border:1px solid rgba(251,191,36,.3);color:#fbbf24}
.outlook.حذر{background:rgba(249,115,22,.1);border:1px solid rgba(249,115,22,.3);color:#fb923c}
.section{margin:24px 0}
.section h2{font-size:16px;color:#94a3b8;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid rgba(148,163,184,.15);display:flex;justify-content:space-between;align-items:center}
.section h2 .badge{font-size:10px;background:rgba(52,211,153,.15);color:#34d399;padding:3px 8px;border-radius:6px;font-weight:700}
.card{background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.01));border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:14px;margin:10px 0;transition:all .2s}
.card:hover{border-color:rgba(52,211,153,.35);transform:translateY(-1px)}
.card .head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.card .ticker{font-size:18px;font-weight:800;color:#e2e8f0}
.card .sector{font-size:10px;color:#64748b;background:rgba(100,116,139,.15);padding:3px 10px;border-radius:8px}
.card .score{font-size:12px;font-weight:700;padding:4px 12px;border-radius:8px}
.card .score.high{background:rgba(52,211,153,.15);color:#34d399}
.card .score.mid{background:rgba(251,191,36,.15);color:#fbbf24}
.card .score.low{background:rgba(148,163,184,.15);color:#94a3b8}
.card .meta{display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:#94a3b8;margin:8px 0}
.card .meta span{background:rgba(255,255,255,.04);padding:3px 9px;border-radius:6px;border:1px solid rgba(255,255,255,.03)}
.card .meta span.ml{background:rgba(129,140,248,.12);color:#818cf8;border-color:rgba(129,140,248,.3)}
.card .meta span.ev-pos{background:rgba(52,211,153,.12);color:#34d399;border-color:rgba(52,211,153,.3)}
.card .meta span.ev-neg{background:rgba(248,113,113,.12);color:#f87171;border-color:rgba(248,113,113,.3)}
.card .reason{font-size:12px;color:#cbd5e1;margin-top:8px;line-height:1.7}
.card .levels{display:flex;gap:10px;margin-top:10px;font-size:11px;flex-wrap:wrap}
.card .levels .stop{color:#f87171;background:rgba(248,113,113,.1);padding:3px 10px;border-radius:6px}
.card .levels .target{color:#34d399;background:rgba(52,211,153,.1);padding:3px 10px;border-radius:6px}
.eval-card{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.05);border-radius:8px;padding:10px 14px;margin:6px 0;display:grid;grid-template-columns:40px 70px 1fr 1fr 1fr;gap:10px;align-items:center;font-size:12px}
.eval-card .hit{color:#34d399}.eval-card .miss{color:#f87171}
.sig-bar{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:11px}
.sig-bar .name{width:120px;color:#94a3b8;text-align:right}
.sig-bar .bar-bg{flex:1;height:7px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden}
.sig-bar .bar-fill{height:100%;border-radius:4px;transition:width .3s}
.sig-bar .pct{width:110px;font-size:11px}
.gainer{display:flex;justify-content:space-between;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,.03);font-size:12px;align-items:center}
.gainer:last-child{border:0}.gainer .chg{color:#34d399;font-weight:700}
.ai-comment{background:linear-gradient(135deg,rgba(52,211,153,.05),rgba(34,211,238,.03));border:1px solid rgba(52,211,153,.15);border-radius:12px;padding:14px;margin:12px 0;font-size:13px;line-height:1.8}
.ai-comment .label{font-size:11px;color:#34d399;font-weight:700;margin-bottom:6px;letter-spacing:.5px}
.ts{text-align:center;font-size:10px;color:#475569;margin-top:32px;padding-top:16px;border-top:1px solid rgba(255,255,255,.05)}
.empty{text-align:center;color:#475569;font-size:13px;padding:20px}
.macro-strip{display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin:14px 0;font-size:11px}
.macro-strip .item{background:rgba(255,255,255,.03);padding:6px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.04)}
.macro-strip .up{color:#34d399}.macro-strip .dn{color:#f87171}
.sector-row{display:grid;grid-template-columns:110px 1fr 100px 100px;gap:10px;align-items:center;padding:6px 0;font-size:11px;border-bottom:1px solid rgba(255,255,255,.03)}
.sector-row .sname{color:#cbd5e1}
.sector-row .sbar{height:8px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden;position:relative}
.sector-row .sfill{height:100%;border-radius:4px;position:absolute;right:0}
.sector-row .sval{text-align:left}
.sector-row .sflow{text-align:left;font-size:10px;color:#64748b}
.ml-card{background:linear-gradient(135deg,rgba(129,140,248,.08),rgba(236,72,153,.03));border:1px solid rgba(129,140,248,.2);border-radius:12px;padding:14px;margin:10px 0}
.ml-card .ml-row{display:flex;justify-content:space-between;padding:4px 0;font-size:12px}
.ml-card .ml-row .label{color:#94a3b8}
.ml-card .ml-row .value{color:#818cf8;font-weight:700}
.catchup{background:rgba(34,211,238,.05);border:1px solid rgba(34,211,238,.2);border-radius:10px;padding:10px 14px;margin:6px 0;font-size:12px}
.catchup .leader{color:#34d399;font-weight:700}
.catchup .laggard{color:#fbbf24;font-weight:700}
.rotation{background:rgba(249,115,22,.05);border:1px solid rgba(249,115,22,.15);border-radius:10px;padding:10px 14px;margin:6px 0;font-size:12px}
"""


SIG_AR = {
    "rsi": "RSI", "stoch_rsi": "StochRSI", "macd": "MACD", "bollinger": "بولنجر",
    "obv": "OBV", "vwap": "VWAP", "volume_surge": "حجم", "sma_cross": "تقاطع ذهبي",
    "breakout": "اختراق", "candle_pattern": "شموع", "oil_correlation": "نفط",
    "adx": "ADX", "supertrend": "Supertrend", "ichimoku": "إيشيموكو",
    "mfi": "MFI", "cmf": "CMF", "fibonacci": "فيبوناتشي",
    "relative_strength": "قوة نسبية", "weekly_trend": "اتجاه أسبوعي",
}


def mc(v):
    return "up" if v > 0 else "dn"


def build_report():
    cdata = load_json(BASE / "tasi_candidates.json", {})
    ai = load_json(BASE / "ai_result.json", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    macro = cdata.get("macro", {})
    gainers = cdata.get("gainers", [])
    eval_results = cdata.get("eval_results", [])
    eval_summary = cdata.get("eval_summary", "")
    signal_acc = cdata.get("signal_accuracy", {})
    sector_summary = cdata.get("sector_summary", {})
    intermarket = cdata.get("intermarket", {})
    ml_metrics = cdata.get("ml_metrics", {})
    importance = cdata.get("feature_importance", {})

    ai_picks = ai.get("picks", [])
    outlook = ai.get("market_outlook", "محايد")
    comment = ai.get("market_comment", "")
    learning = ai.get("learning_notes", "")
    missed = ai.get("missed_analysis", "")
    sector_ai = ai.get("sector_analysis", "")
    global_ai = ai.get("global_impact", "")
    catchup_ai = ai.get("catch_up_opportunities", "")
    risks = ai.get("risks_to_watch", "")
    no_ai = ai.get("no_ai", False)

    # ═══════════════════════════════════════════
    # Outlook + Macro
    # ═══════════════════════════════════════════
    outlook_html = f'<div class="outlook {outlook}">📊 نظرة السوق: {outlook}</div>'

    macro_html = f"""<div class="macro-strip">
        <div class="item">🛢 النفط: {macro.get('oil','N/A')} <span class="{mc(macro.get('oil_chg',0))}">{macro.get('oil_chg',0):+.2f}%</span></div>
        <div class="item">🥇 الذهب: {macro.get('gold','N/A')} <span class="{mc(macro.get('gold_chg',0))}">{macro.get('gold_chg',0):+.2f}%</span></div>
        <div class="item">📈 S&P: {macro.get('sp500','N/A')} <span class="{mc(macro.get('sp500_chg',0))}">{macro.get('sp500_chg',0):+.2f}%</span></div>
        <div class="item">😰 VIX: {macro.get('vix','N/A')}</div>
        <div class="item">📊 سندات 10Y: {macro.get('us10y','N/A')} <span class="{mc(macro.get('us10y_chg',0))}">{macro.get('us10y_chg',0):+.2f}%</span></div>
        <div class="item">💵 DXY: {macro.get('dxy','N/A')} <span class="{mc(macro.get('dxy_chg',0))}">{macro.get('dxy_chg',0):+.2f}%</span></div>
        <div class="item">🇸🇦 TASI: {macro.get('tasi_index','N/A')} <span class="{mc(macro.get('tasi_chg',0))}">{macro.get('tasi_chg',0):+.2f}%</span></div>
    </div>"""

    # ═══════════════════════════════════════════
    # AI comments
    # ═══════════════════════════════════════════
    ai_html = ""
    if comment:
        ai_html += f'<div class="ai-comment"><div class="label">🧠 تعليق Claude Opus 4.7</div>{comment}</div>'
    if sector_ai:
        ai_html += f'<div class="ai-comment"><div class="label">🏭 تحليل القطاعات</div>{sector_ai}</div>'
    if global_ai:
        ai_html += f'<div class="ai-comment"><div class="label">🌍 تأثير المؤشرات العالمية</div>{global_ai}</div>'
    if catchup_ai:
        ai_html += f'<div class="ai-comment"><div class="label">⚡ فرص Catch-up</div>{catchup_ai}</div>'
    if risks:
        ai_html += f'<div class="ai-comment"><div class="label">⚠️ مخاطر للمراقبة</div>{risks}</div>'
    if learning:
        ai_html += f'<div class="ai-comment"><div class="label">📚 ملاحظات التعلم</div>{learning}</div>'
    if missed:
        ai_html += f'<div class="ai-comment"><div class="label">🔍 فرص فاتتنا</div>{missed}</div>'
    if no_ai:
        ai_html += '<div class="ai-comment"><div class="label">⚠️</div>Claude AI غير متاح — نتائج فنية فقط</div>'

    # ═══════════════════════════════════════════
    # ML section
    # ═══════════════════════════════════════════
    ml_html = ""
    if ml_metrics and ml_metrics.get("samples_total"):
        ml_html = f"""<div class="section"><h2>🤖 نموذج التعلم الآلي (XGBoost) <span class="badge">{ml_metrics.get('samples_total',0)} عينة</span></h2>
        <div class="ml-card">
          <div class="ml-row"><span class="label">دقة النموذج (Accuracy)</span><span class="value">{ml_metrics.get('accuracy','-')}</span></div>
          <div class="ml-row"><span class="label">ROC-AUC</span><span class="value">{ml_metrics.get('roc_auc','-')}</span></div>
          <div class="ml-row"><span class="label">Precision</span><span class="value">{ml_metrics.get('precision','-')}</span></div>
          <div class="ml-row"><span class="label">Recall</span><span class="value">{ml_metrics.get('recall','-')}</span></div>
          <div class="ml-row"><span class="label">F1 Score</span><span class="value">{ml_metrics.get('f1','-')}</span></div>
          <div class="ml-row"><span class="label">نسبة إيجابية في البيانات</span><span class="value">{ml_metrics.get('positive_ratio','-')}</span></div>
        </div>"""
        # Top features
        if importance:
            ml_html += '<div style="margin-top:12px"><div style="font-size:11px;color:#94a3b8;margin-bottom:6px">أهم المميزات:</div>'
            top = list(importance.items())[:10]
            max_imp = max(v for _, v in top) if top else 1
            for feat, imp in top:
                w = (imp / max_imp * 100) if max_imp > 0 else 0
                ml_html += f'<div class="sig-bar"><span class="name">{feat}</span><div class="bar-bg"><div class="bar-fill" style="width:{w}%;background:#818cf8"></div></div><span class="pct">{imp:.3f}</span></div>'
            ml_html += '</div>'
        ml_html += '</div>'

    # ═══════════════════════════════════════════
    # AI Picks
    # ═══════════════════════════════════════════
    pick_cards = ""
    if ai_picks:
        for p in ai_picks:
            conf = p.get("confidence", p.get("score", 50))
            cls = "high" if isinstance(conf, (int, float)) and conf >= 70 else \
                  "mid" if isinstance(conf, (int, float)) and conf >= 50 else "low"

            ticker = p.get("ticker", "")
            sector = p.get("sector", "")
            close_val = p.get("close", "")
            reason = p.get("reason", " • ".join(p.get("reasons", [])[:3]))
            stop = p.get("stop", "")
            target = p.get("target", p.get("target1", ""))
            target2 = p.get("target2", "")
            action = p.get("action", "شراء")
            days = p.get("holding_days", "")
            risk_level = p.get("risk_level", "")
            ml_prob = p.get("ml_probability")
            ev = p.get("expected_value_pct")
            rr = p.get("risk_reward")

            rsi = p.get("rsi", "")
            adx_v = p.get("adx", "")
            mfi_v = p.get("mfi", "")
            vol_r = p.get("volume_ratio", "")
            wt = p.get("weekly_trend", "")

            meta = ""
            if close_val:
                meta += f"<span>💰 {close_val}</span>"
            if rsi != "":
                meta += f"<span>RSI:{rsi}</span>"
            if adx_v != "":
                meta += f"<span>ADX:{adx_v}</span>"
            if mfi_v != "":
                meta += f"<span>MFI:{mfi_v}</span>"
            if vol_r != "":
                meta += f"<span>حجم:{vol_r}×</span>"
            if wt:
                meta += f"<span>📅 {wt}</span>"
            if action:
                meta += f"<span>🎯 {action}</span>"
            if days:
                meta += f"<span>{days} يوم</span>"
            if risk_level:
                meta += f"<span>⚠️ {risk_level}</span>"
            if ml_prob is not None:
                meta += f'<span class="ml">🤖 ML: {ml_prob*100:.0f}%</span>'
            if ev is not None:
                ev_cls = "ev-pos" if ev > 0 else "ev-neg"
                meta += f'<span class="{ev_cls}">EV: {ev:+.2f}%</span>'
            if rr is not None:
                meta += f"<span>R:R 1:{rr}</span>"

            lvl = ""
            if stop or target or target2:
                lvl = '<div class="levels">'
                if stop:
                    lvl += f'<span class="stop">⛔ وقف {stop}</span>'
                if target:
                    lvl += f'<span class="target">🎯 هدف1 {target}</span>'
                if target2:
                    lvl += f'<span class="target">🎯 هدف2 {target2}</span>'
                lvl += '</div>'

            pick_cards += f'''<div class="card"><div class="head">
                <span class="ticker">{ticker}</span>
                <span class="sector">{sector}</span>
                <span class="score {cls}">ثقة {conf}%</span>
            </div>
            <div class="meta">{meta}</div>
            <div class="reason">{reason}</div>{lvl}</div>'''
    else:
        pick_cards = '<p class="empty">لا توجد فرص واضحة</p>'

    # ═══════════════════════════════════════════
    # Sector flow (intermarket)
    # ═══════════════════════════════════════════
    flow_html = ""
    sector_flows = intermarket.get("sector_flows", {})
    if sector_flows:
        flow_html = '<div class="section"><h2>💰 تدفق السيولة (5 أيام) <span class="badge">بالمليون ريال</span></h2>'
        sorted_flows = sorted(sector_flows.items(), key=lambda x: -x[1].get("net_flow_5d", 0))
        max_flow = max(abs(f.get("net_flow_5d", 0)) for _, f in sorted_flows) or 1
        for sec, info in sorted_flows:
            flow = info.get("net_flow_5d", 0)
            chg = info.get("avg_change_5d", 0)
            w = min(abs(flow) / max_flow * 100, 100)
            color = "#34d399" if flow > 0 else "#f87171"
            flow_html += f'''<div class="sector-row">
                <span class="sname">{sec}</span>
                <div class="sbar"><div class="sfill" style="width:{w}%;background:{color}"></div></div>
                <span class="sval" style="color:{color}">{flow:+,.1f}M</span>
                <span class="sflow">{info.get('momentum_trend','')} | قائد: {info.get('leader','')}</span>
            </div>'''
        flow_html += '</div>'

    # Catch-up opportunities
    catchup_html = ""
    divergent = intermarket.get("divergent_pairs", [])
    leader_laggard = intermarket.get("leader_laggard", {})
    ll_opps = [(s, i) for s, i in leader_laggard.items() if i.get("catch_up_opportunity")]

    if divergent or ll_opps:
        catchup_html = '<div class="section"><h2>⚡ فرص Catch-up (المتأخر قد يلحق القائد)</h2>'

        if divergent:
            for d in divergent[:6]:
                catchup_html += f'''<div class="catchup">
                    <span class="leader">{d['leader']}</span> قفز {d['leader_chg']:+.1f}% |
                    <span class="laggard">{d['laggard']}</span> فقط {d['laggard_chg']:+.1f}%
                    (ارتباط تاريخي {d['corr']}) → الفجوة {d['spread']:+.1f}%
                </div>'''

        if ll_opps:
            for sec, info in ll_opps[:6]:
                catchup_html += f'''<div class="catchup">
                    <b>{sec}</b>: <span class="leader">{info['leader']}</span> ({info['leader_change']:+.1f}%) •
                    <span class="laggard">{info['laggard']}</span> ({info['laggard_change']:+.1f}%) •
                    فارق {info['spread']:+.1f}%
                </div>'''

        catchup_html += '</div>'

    # Sector rotation
    rotation_html = ""
    rotations = intermarket.get("sector_rotation", [])
    if rotations:
        rotation_html = '<div class="section"><h2>🔄 دوران قطاعي مكتشف</h2>'
        for r in rotations[:6]:
            rotation_html += f'''<div class="rotation">
                <b>{r['sector']}</b>: {r['rotation']} |
                أمس: {r['prev_change']:+.1f}% → اليوم: {r['now_change']:+.1f}%
                (تغيير: {r['delta']:+.1f}%)
            </div>'''
        rotation_html += '</div>'

    # Sector summary bars
    sector_html = ""
    if sector_summary:
        sector_html = '<div class="section"><h2>🏭 أداء القطاعات اليوم</h2>'
        for sec, info in sorted(sector_summary.items(), key=lambda x: -x[1].get("avg_change", 0)):
            ch = info.get("avg_change", 0)
            color = "#34d399" if ch > 0 else "#f87171"
            w = min(abs(ch) * 20, 100)
            sector_html += f'''<div class="sector-row">
                <span class="sname">{sec}</span>
                <div class="sbar"><div class="sfill" style="width:{w}%;background:{color}"></div></div>
                <span class="sval" style="color:{color}">{ch:+.2f}%</span>
                <span class="sflow">{info.get('pct_gainers',0):.0f}% مرتفعة ({info.get('count',0)} سهم)</span>
            </div>'''
        sector_html += '</div>'

    # Eval
    eval_html = ""
    if eval_results:
        eval_html = f'<div class="section"><h2>📋 تقييم الأمس (تعريف واقعي: +1.5% في 3 أيام دون -2%) <span class="badge">{eval_summary}</span></h2>'
        for r in eval_results[:20]:
            cls = "hit" if r["hit"] else "miss"
            icon = "✅" if r["hit"] else "❌"
            eval_html += f'''<div class="eval-card">
                <span class="{cls}">{icon}</span>
                <span>{r["ticker"]}</span>
                <span>دخول: {r["predicted_close"]}</span>
                <span class="{cls}">أعلى: {r.get("max_high","-")} ({r.get("max_pct",0):+.1f}%)</span>
                <span class="{cls}">أدنى: {r.get("min_low","-")} ({r.get("min_pct",0):+.1f}%)</span>
            </div>'''
        eval_html += '</div>'

    # Signal accuracy
    sig_html = ""
    if signal_acc:
        sig_html = '<div class="section"><h2>📊 دقة كل إشارة (بناءً على hits حقيقية)</h2>'
        for sig, acc in sorted(signal_acc.items(), key=lambda x: -x[1].get("rate", 0)):
            if acc.get("triggered", 0) > 0:
                rate = acc["rate"] * 100
                color = "#34d399" if rate >= 55 else "#fbbf24" if rate >= 40 else "#f87171"
                sig_html += f'<div class="sig-bar"><span class="name">{SIG_AR.get(sig,sig)}</span><div class="bar-bg"><div class="bar-fill" style="width:{rate}%;background:{color}"></div></div><span class="pct" style="color:{color}">{rate:.0f}% ({acc["hit"]}/{acc["triggered"]})</span></div>'
        sig_html += '</div>'

    # Gainers
    g_html = ""
    if gainers:
        g_html = '<div class="section"><h2>🔥 أعلى 15 ارتفاعاً اليوم</h2>'
        for g in gainers[:15]:
            g_html += f'<div class="gainer"><span><b>{g["ticker"]}</b> <span style="color:#475569;font-size:10px">({g["sector"]})</span></span><span>{g["close"]}</span><span class="chg">+{g["change"]}%</span></div>'
        g_html += '</div>'

    # Final HTML
    html = f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ماسح تداول الذكي V9 — Claude Opus 4.7</title>
<style>{CSS}</style></head><body><div class="wrap">
<header>
  <h1>🇸🇦 ماسح تداول الذكي V9</h1>
  <div class="sub">18 مؤشر فني + XGBoost ML + Claude Opus 4.7 + تحليل ارتباطات + تدفق سيولة</div>
</header>
{outlook_html}{macro_html}{ai_html}
<div class="section"><h2>🎯 اختيارات Opus ({len(ai_picks)} سهم)</h2>{pick_cards}</div>
{ml_html}{catchup_html}{rotation_html}{flow_html}{sector_html}{eval_html}{sig_html}{g_html}
<p class="ts">آخر تحديث: {now} | V9 | Opus 4.7 + XGBoost | ⚠️ ليست نصيحة مالية</p>
</div></body></html>"""

    (OUT / "index.html").write_text(html, encoding="utf-8")
    print(f"  ✓ تقرير → public/index.html")


def run():
    build_report()


if __name__ == "__main__":
    run()
