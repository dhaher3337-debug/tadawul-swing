# -*- coding: utf-8 -*-
"""
Weekly Report Generator — V9.2
================================
🎯 الهدف: تقرير أسبوعي يحلل **أداء النظام نفسه** (مش السوق فقط)

مختلف عن التقرير اليومي:
  اليومي → "ماذا أوصي به اليوم؟"
  الأسبوعي → "كيف أدّى نظامي خلال الأسبوع الماضي؟"

محتويات التقرير:
  1. ملخص أداء النظام (Win Rate, Profit Factor, ...)
  2. أفضل/أسوأ صفقات الأسبوع
  3. أداء حسب القطاع
  4. أداء حسب نوع الإشارة
  5. الأسهم المُتعِبة (المتكررة بدون نتائج)
  6. مؤشر نضج Knowledge Capture
  7. الفرص الضائعة (تلقائياً!)
  8. توصيات للأسبوع القادم

كيف يعمل:
  - يُشغَّل يوم الجمعة فجراً (يحلل أسبوع الإثنين-الخميس)
  - يقرأ paper_trades.json + claude_decisions_log.jsonl
  - يقرأ tadawul_data من السبعة أيام الماضية
  - ينتج HTML report + Excel summary

التشغيل:
  python weekly_report.py
"""
import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

BASE = Path("tadawul_data")
PAPER_DIR = Path("paper_trades")
WEEKLY_DIR = Path("weekly_reports")
WEEKLY_DIR.mkdir(parents=True, exist_ok=True)


def get_week_range(reference_date=None):
    """يحدد بداية ونهاية الأسبوع التحليلي.
    
    الأسبوع التحليلي = الأحد للخميس (أيام التداول السعودي)
    عند التشغيل يوم الجمعة، يحلل الأسبوع المنتهي للتو.
    """
    if reference_date is None:
        reference_date = datetime.now()
    
    # weekday(): Mon=0, Sun=6
    # نريد آخر خميس
    weekday = reference_date.weekday()
    if weekday == 4:  # الجمعة
        last_thursday = reference_date - timedelta(days=1)
    elif weekday == 5:  # السبت
        last_thursday = reference_date - timedelta(days=2)
    elif weekday == 6:  # الأحد
        last_thursday = reference_date - timedelta(days=3)
    else:  # الإثنين-الخميس - نأخذ الأسبوع السابق
        days_since_thursday = (weekday - 4) % 7
        last_thursday = reference_date - timedelta(days=days_since_thursday + 7)
    
    week_start = last_thursday - timedelta(days=4)  # الأحد
    return week_start.strftime("%Y-%m-%d"), last_thursday.strftime("%Y-%m-%d")


def analyze_paper_trades_week(week_start, week_end):
    """تحليل صفقات الأسبوع من paper_trades."""
    f_trades = BASE / "paper_trades.json"
    if not f_trades.exists():
        return None
    
    with open(f_trades, encoding='utf-8') as f:
        db = json.load(f)
    
    # الصفقات المغلقة في الأسبوع
    week_closed = [
        t for t in db.get("closed", [])
        if week_start <= t.get("close_date", "") <= week_end
    ]
    
    # الصفقات المفتوحة في الأسبوع
    week_opened = [
        t for t in db.get("closed", []) + db.get("active", [])
        if week_start <= t.get("open_date", "") <= week_end
    ]
    
    if not week_closed and not week_opened:
        return {
            "week_start": week_start, "week_end": week_end,
            "no_data": True,
        }
    
    # إحصاءات
    wins = [t for t in week_closed if "WIN" in t.get("result", "")]
    losses = [t for t in week_closed if "LOSS" in t.get("result", "")]
    
    total = len(week_closed)
    win_rate = len(wins) / total * 100 if total > 0 else 0
    
    avg_win = sum(t["final_pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["final_pnl_pct"] for t in losses) / len(losses) if losses else 0
    
    total_gain = sum(t["final_pnl_pct"] for t in wins)
    total_loss_abs = abs(sum(t["final_pnl_pct"] for t in losses))
    profit_factor = total_gain / total_loss_abs if total_loss_abs > 0 else (float('inf') if total_gain > 0 else 0)
    
    # حسب القطاع
    by_sector = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0})
    for t in week_closed:
        sec = t.get("sector", "?")
        by_sector[sec]["total"] += 1
        by_sector[sec]["pnl"] += t.get("final_pnl_pct", 0)
        if "WIN" in t.get("result", ""):
            by_sector[sec]["wins"] += 1
    
    # حسب نوع الإشارة
    by_signal = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0})
    for t in week_closed:
        st = t.get("signal_type", "default")
        by_signal[st]["total"] += 1
        by_signal[st]["pnl"] += t.get("final_pnl_pct", 0)
        if "WIN" in t.get("result", ""):
            by_signal[st]["wins"] += 1
    
    # أفضل وأسوأ صفقة
    best = max(week_closed, key=lambda x: x["final_pnl_pct"]) if week_closed else None
    worst = min(week_closed, key=lambda x: x["final_pnl_pct"]) if week_closed else None
    
    return {
        "week_start": week_start, "week_end": week_end,
        "total_closed": total,
        "total_opened": len(week_opened),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else None,
        "total_pnl_pct": round(sum(t["final_pnl_pct"] for t in week_closed), 2),
        "best_trade": {
            "ticker": best["ticker"], "pnl": best["final_pnl_pct"],
            "sector": best.get("sector"), "result": best.get("result"),
        } if best else None,
        "worst_trade": {
            "ticker": worst["ticker"], "pnl": worst["final_pnl_pct"],
            "sector": worst.get("sector"), "result": worst.get("result"),
        } if worst else None,
        "by_sector": {k: {**v, "win_rate": round(v["wins"]/v["total"]*100, 1) if v["total"] else 0}
                       for k, v in by_sector.items()},
        "by_signal": {k: {**v, "win_rate": round(v["wins"]/v["total"]*100, 1) if v["total"] else 0}
                      for k, v in by_signal.items()},
    }


def analyze_knowledge_growth(week_start, week_end):
    """تحليل نمو قاعدة المعرفة خلال الأسبوع."""
    f_log = BASE / "claude_decisions_log.jsonl"
    if not f_log.exists():
        return {"no_data": True}
    
    week_records = []
    total_records = 0
    with open(f_log, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            total_records += 1
            if week_start <= rec.get("date", "") <= week_end:
                week_records.append(rec)
    
    by_date = defaultdict(lambda: {"buys": 0, "skips": 0})
    for r in week_records:
        d = r["date"]
        action = r.get("claude_decision", {}).get("action", "?")
        if action == "buy":
            by_date[d]["buys"] += 1
        elif action == "skip":
            by_date[d]["skips"] += 1
    
    # كم منها مرتبط بـ outcomes؟
    with_outcomes = [r for r in week_records if r.get("actual_outcome")]
    
    return {
        "total_lifetime": total_records,
        "this_week": len(week_records),
        "buys_this_week": sum(d["buys"] for d in by_date.values()),
        "skips_this_week": sum(d["skips"] for d in by_date.values()),
        "with_outcomes": len(with_outcomes),
        "by_date": dict(by_date),
    }


def find_tired_stocks(week_start, week_end):
    """الأسهم المُتعِبة: تكررت توصيتها بدون نتائج جيدة."""
    f_log = BASE / "claude_decisions_log.jsonl"
    if not f_log.exists():
        return []
    
    by_ticker = defaultdict(list)
    with open(f_log, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not (week_start <= rec.get("date", "") <= week_end):
                continue
            if rec.get("claude_decision", {}).get("action") != "buy":
                continue
            by_ticker[rec["ticker"]].append(rec)
    
    tired = []
    for tk, records in by_ticker.items():
        if len(records) < 2:
            continue
        # احسب outcomes
        with_out = [r for r in records if r.get("actual_outcome")]
        if not with_out:
            continue
        wins = sum(1 for r in with_out if "WIN" in r["actual_outcome"].get("result", ""))
        if wins / len(with_out) < 0.3:
            tired.append({
                "ticker": tk,
                "times_recommended": len(records),
                "outcomes_known": len(with_out),
                "wins": wins,
                "win_rate": round(wins / len(with_out) * 100, 1) if with_out else 0,
            })
    
    return sorted(tired, key=lambda x: x["win_rate"])


def build_html_report(week_data, knowledge_data, tired_stocks, week_start, week_end):
    """بناء تقرير HTML أسبوعي."""
    
    # أداء حسب القطاع - HTML
    sector_rows = ""
    if not week_data.get("no_data"):
        for sec, d in sorted(week_data.get("by_sector", {}).items(), key=lambda x: -x[1]["pnl"]):
            color = "#c6efce" if d["pnl"] > 0 else "#ffc7ce"
            sector_rows += f"""
            <tr style="background:{color}">
              <td>{sec}</td>
              <td style="text-align:center">{d['total']}</td>
              <td style="text-align:center">{d['wins']}</td>
              <td style="text-align:center">{d['win_rate']}%</td>
              <td style="text-align:center">{d['pnl']:+.2f}%</td>
            </tr>"""
    
    # حسب نوع الإشارة
    signal_rows = ""
    if not week_data.get("no_data"):
        for sig, d in sorted(week_data.get("by_signal", {}).items(), key=lambda x: -x[1]["win_rate"]):
            color = "#c6efce" if d["win_rate"] >= 50 else "#fff2cc" if d["win_rate"] >= 30 else "#ffc7ce"
            signal_rows += f"""
            <tr style="background:{color}">
              <td>{sig}</td>
              <td style="text-align:center">{d['total']}</td>
              <td style="text-align:center">{d['wins']}</td>
              <td style="text-align:center">{d['win_rate']}%</td>
              <td style="text-align:center">{d['pnl']:+.2f}%</td>
            </tr>"""
    
    # تكرار اليومي للقرارات
    daily_decisions = ""
    if knowledge_data.get("by_date"):
        for d in sorted(knowledge_data["by_date"].keys()):
            counts = knowledge_data["by_date"][d]
            daily_decisions += f"<li>{d}: {counts['buys']} buy + {counts['skips']} skip</li>"
    
    # الأسهم المتعبة
    tired_html = ""
    if tired_stocks:
        for t in tired_stocks:
            tired_html += f"""
            <tr style="background:#ffc7ce">
              <td>{t['ticker']}</td>
              <td style="text-align:center">{t['times_recommended']}</td>
              <td style="text-align:center">{t['wins']}/{t['outcomes_known']}</td>
              <td style="text-align:center">{t['win_rate']}%</td>
            </tr>"""
    else:
        tired_html = '<tr><td colspan="4" style="text-align:center;color:#666">لا يوجد - النظام صحي 👍</td></tr>'
    
    # الـ best/worst
    best = week_data.get("best_trade") or {}
    worst = week_data.get("worst_trade") or {}
    
    # Win rate color
    wr = week_data.get("win_rate", 0)
    wr_color = "#c6efce" if wr >= 60 else "#fff2cc" if wr >= 50 else "#ffc7ce"
    
    html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<title>📊 التقرير الأسبوعي V9.2 - {week_start} إلى {week_end}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 1100px; margin: 20px auto; padding: 20px; background: #f5f5f5; }}
  h1 {{ color: #1f4e78; border-bottom: 3px solid #1f4e78; padding-bottom: 10px; }}
  h2 {{ color: #2e75b6; margin-top: 30px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin: 20px 0; }}
  .kpi {{ background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); text-align: center; }}
  .kpi-value {{ font-size: 32px; font-weight: bold; color: #1f4e78; }}
  .kpi-label {{ color: #666; margin-top: 8px; font-size: 14px; }}
  table {{ width: 100%; border-collapse: collapse; background: white; box-shadow: 0 2px 8px rgba(0,0,0,0.05); border-radius: 8px; overflow: hidden; }}
  th {{ background: #1f4e78; color: white; padding: 12px; }}
  td {{ padding: 10px; border-bottom: 1px solid #eee; }}
  .section {{ background: white; padding: 20px; border-radius: 10px; margin: 20px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
  .badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 13px; font-weight: bold; }}
  .badge-win {{ background: #c6efce; color: #006100; }}
  .badge-loss {{ background: #ffc7ce; color: #9c0006; }}
  .insight {{ background: #fff9e6; border-right: 4px solid #f4a261; padding: 15px; margin: 15px 0; border-radius: 6px; }}
</style>
</head>
<body>
  <h1>📊 التقرير الأسبوعي V9.2</h1>
  <p>الفترة: <strong>{week_start}</strong> إلى <strong>{week_end}</strong> (أيام التداول)</p>
  <p>تم إنشاؤه: {datetime.now():%Y-%m-%d %H:%M}</p>
"""
    
    if week_data.get("no_data"):
        html += """
  <div class="section">
    <h2>⚠️ لا توجد بيانات صفقات لهذا الأسبوع</h2>
    <p>الأسبوع الأول من التشغيل، أو لم تُغلق أي صفقات بعد.</p>
  </div>
"""
    else:
        html += f"""
  <h2>📈 ملخص الأداء</h2>
  <div class="kpi-grid">
    <div class="kpi" style="background:{wr_color}">
      <div class="kpi-value">{week_data['win_rate']}%</div>
      <div class="kpi-label">Win Rate</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{week_data['total_closed']}</div>
      <div class="kpi-label">صفقات مُغلقة</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{week_data.get('profit_factor', 'N/A')}</div>
      <div class="kpi-label">Profit Factor</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{week_data['total_pnl_pct']:+.2f}%</div>
      <div class="kpi-label">إجمالي P&amp;L</div>
    </div>
    <div class="kpi" style="background:#c6efce">
      <div class="kpi-value">+{week_data['avg_win_pct']:.2f}%</div>
      <div class="kpi-label">متوسط الربح</div>
    </div>
    <div class="kpi" style="background:#ffc7ce">
      <div class="kpi-value">{week_data['avg_loss_pct']:.2f}%</div>
      <div class="kpi-label">متوسط الخسارة</div>
    </div>
  </div>
  
  <div class="section">
    <h2>🏆 أفضل وأسوأ صفقة</h2>
    <p>
      <span class="badge badge-win">🥇 أفضل</span>
      <strong>{best.get('ticker', '-')}</strong> ({best.get('sector', '-')}): 
      <strong style="color:#006100">{best.get('pnl', 0):+.2f}%</strong>
      ({best.get('result', '')})
    </p>
    <p>
      <span class="badge badge-loss">📉 أسوأ</span>
      <strong>{worst.get('ticker', '-')}</strong> ({worst.get('sector', '-')}): 
      <strong style="color:#9c0006">{worst.get('pnl', 0):+.2f}%</strong>
      ({worst.get('result', '')})
    </p>
  </div>
  
  <div class="section">
    <h2>🏢 الأداء حسب القطاع</h2>
    <table>
      <thead>
        <tr><th>القطاع</th><th>صفقات</th><th>فوز</th><th>Win Rate</th><th>إجمالي P&amp;L</th></tr>
      </thead>
      <tbody>{sector_rows or '<tr><td colspan="5" style="text-align:center">لا توجد بيانات</td></tr>'}</tbody>
    </table>
  </div>
  
  <div class="section">
    <h2>📊 الأداء حسب نوع الإشارة</h2>
    <table>
      <thead>
        <tr><th>نوع الإشارة</th><th>صفقات</th><th>فوز</th><th>Win Rate</th><th>إجمالي P&amp;L</th></tr>
      </thead>
      <tbody>{signal_rows or '<tr><td colspan="5" style="text-align:center">لا توجد بيانات</td></tr>'}</tbody>
    </table>
  </div>
"""
    
    # قسم Knowledge
    if not knowledge_data.get("no_data"):
        html += f"""
  <h2>🧠 نمو قاعدة المعرفة (Knowledge Capture)</h2>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-value">{knowledge_data['total_lifetime']}</div>
      <div class="kpi-label">إجمالي القرارات (lifetime)</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{knowledge_data['this_week']}</div>
      <div class="kpi-label">قرارات هذا الأسبوع</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{knowledge_data['buys_this_week']}</div>
      <div class="kpi-label">Buy decisions</div>
    </div>
    <div class="kpi">
      <div class="kpi-value">{knowledge_data['with_outcomes']}</div>
      <div class="kpi-label">مرتبطة بنتائج</div>
    </div>
  </div>
  
  <div class="insight">
    <strong>💡 ملاحظة:</strong> كل قرار محفوظ هو خطوة نحو استقلالية النظام. 
    الهدف: 1500+ قرار خلال 6 أشهر لبناء نموذج ML بديل لكلود.
  </div>
"""
    
    # الأسهم المتعبة
    html += f"""
  <div class="section">
    <h2>⚠️ الأسهم المُتعِبة هذا الأسبوع</h2>
    <p style="color:#666">أسهم تكررت توصيتها مرتين+ بـ win rate أقل من 30%</p>
    <table>
      <thead>
        <tr><th>السهم</th><th>عدد التوصيات</th><th>Wins/Total</th><th>Win Rate</th></tr>
      </thead>
      <tbody>{tired_html}</tbody>
    </table>
  </div>
  
  <div class="section">
    <h2>📌 توصيات للأسبوع القادم</h2>
    <ul>
"""
    
    # توصيات ذكية بناءً على البيانات
    if not week_data.get("no_data"):
        if week_data.get("win_rate", 0) >= 60:
            html += "<li>✅ <strong>أداء قوي</strong> - استمر بنفس النهج، النظام يعمل بكفاءة.</li>"
        elif week_data.get("win_rate", 0) < 40:
            html += "<li>⚠️ <strong>أداء ضعيف</strong> - راجع نوع الإشارات الخاسرة، قد تحتاج تشديد المعايير.</li>"
        
        # القطاع الأقوى
        if week_data.get("by_sector"):
            best_sector = max(week_data["by_sector"].items(), key=lambda x: x[1]["pnl"])
            html += f"<li>🏆 <strong>{best_sector[0]}</strong> القطاع الأقوى ({best_sector[1]['pnl']:+.2f}%) - فكر بزيادة التركيز.</li>"
        
        # نوع إشارة قوي
        if week_data.get("by_signal"):
            best_signal = max(week_data["by_signal"].items(), key=lambda x: x[1]["win_rate"])
            if best_signal[1]["total"] >= 2:
                html += f"<li>📊 <strong>{best_signal[0]}</strong> أقوى نوع إشارة (Win Rate {best_signal[1]['win_rate']}%).</li>"
    
    if tired_stocks:
        tickers = ", ".join(t["ticker"] for t in tired_stocks[:3])
        html += f"<li>🚫 تجنب الأسهم المتعبة: <strong>{tickers}</strong> - النظام بدأ يخفض scoreها تلقائياً.</li>"
    
    if knowledge_data.get("this_week", 0) > 0:
        completion = knowledge_data.get("with_outcomes", 0) / max(knowledge_data["this_week"], 1) * 100
        if completion < 30:
            html += f"<li>⏳ {completion:.0f}% فقط من قرارات الأسبوع لها outcomes - أغلب الصفقات مازالت مفتوحة (طبيعي).</li>"
    
    html += """
    </ul>
  </div>
  
  <div style="text-align:center; color:#666; margin-top:40px; padding:20px; border-top:1px solid #ddd">
    <p>📊 تقرير V9.2 الأسبوعي - مولّد تلقائياً</p>
    <p>للأسئلة: راجع paper_trades/latest.xlsx + tadawul_data/claude_decisions_log.jsonl</p>
  </div>
</body>
</html>
"""
    
    return html


def run():
    """التشغيل الرئيسي."""
    print("=" * 60)
    print("📊 التقرير الأسبوعي V9.2")
    print("=" * 60)
    
    week_start, week_end = get_week_range()
    print(f"\n📅 الفترة: {week_start} إلى {week_end}")
    
    # تحليل
    print("\n🔍 تحليل الصفقات...")
    week_data = analyze_paper_trades_week(week_start, week_end)
    
    print("🔍 تحليل قاعدة المعرفة...")
    knowledge_data = analyze_knowledge_growth(week_start, week_end)
    
    print("🔍 البحث عن الأسهم المُتعِبة...")
    tired = find_tired_stocks(week_start, week_end)
    
    # ملخص في الكونسول
    if week_data and not week_data.get("no_data"):
        print(f"\n📊 ملخص:")
        print(f"   Win Rate: {week_data['win_rate']}%")
        print(f"   Total: {week_data['total_closed']} مُغلقة")
        print(f"   P&L: {week_data['total_pnl_pct']:+.2f}%")
        print(f"   Profit Factor: {week_data.get('profit_factor', 'N/A')}")
    else:
        print("\n⚠️ لا توجد بيانات صفقات لهذا الأسبوع")
    
    if not knowledge_data.get("no_data"):
        print(f"\n🧠 Knowledge: {knowledge_data['this_week']} قرار هذا الأسبوع | {knowledge_data['total_lifetime']} إجمالي")
    
    if tired:
        print(f"\n⚠️ {len(tired)} سهم متعب: {[t['ticker'] for t in tired]}")
    
    # بناء HTML
    print("\n📄 بناء التقرير...")
    html = build_html_report(week_data or {}, knowledge_data, tired, week_start, week_end)
    
    output_path = WEEKLY_DIR / f"weekly_{week_end}.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    # latest نسخة سهلة الوصول
    latest_path = WEEKLY_DIR / "latest.html"
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"\n✅ التقرير: {output_path}")
    print(f"✅ الأخير: {latest_path}")
    
    return output_path


if __name__ == "__main__":
    run()
