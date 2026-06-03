# -*- coding: utf-8 -*-
"""
backtest_strategies.py — مقارنة استراتيجيات الدخول/الفلترة
===========================================================
يقارن على بيانات universe_snapshots (خالية من survivorship، فيها عوائد
مستقبلية 3 أيام بعد evaluate_universe):

  A) القاعدة V9.2.4        : عتبة score نسبية + ADX + RS + anti-chasing، top-N
  B) A + فلتر النظام السوقي : لا صفقات حين اتجاه السوق (trailing 3d) سلبي
  C) A + جودة الدخول        : تقييد التمدد فوق SMA20 (بديل عن pullback)
  D) B + C

⚠️ القيود (يجب ذكرها): النافذة قصيرة (~9 أيام، نظام سوقي واحد غالباً هابط).
   النتائج إرشادية لا قاطعة. أعد التشغيل كلما تراكمت snapshots/بيانات data-lake.
"""
import json, glob, statistics as st
from collections import defaultdict

SNAP_DIR = "tadawul_data/universe_snapshots"
TOP_N = 5
TARGET = 2.0  # % لتعريف "إصابة"


def load_days():
    days = {}
    for fp in sorted(glob.glob(f"{SNAP_DIR}/*.jsonl")):
        d = fp.split("snapshot_")[1].replace(".jsonl", "")
        rows = [json.loads(l) for l in open(fp, encoding="utf-8") if l.strip()]
        days[d] = rows
    return dict(sorted(days.items()))


def market_regime(days):
    """اتجاه السوق لكل يوم = إشارة عائد السوق التراكمي 3 أيام (بلا lookahead).
    عائد السوق اليومي = متوسط change_pct عبر الكون."""
    dates = list(days.keys())
    daily_mkt = {}
    for d in dates:
        chgs = [r.get("change_pct") for r in days[d] if r.get("change_pct") is not None]
        daily_mkt[d] = st.mean(chgs) if chgs else 0.0
    regime = {}
    for i, d in enumerate(dates):
        window = [daily_mkt[dates[j]] for j in range(max(0, i - 2), i + 1)]
        regime[d] = sum(window)  # موجب = سوق صاعد
    return regime, daily_mkt


def passes_base(r):
    """بوابة V9.2.4 مبسّطة على حقول الـ snapshot."""
    f = r.get("features", {}) or {}
    score = r.get("score_raw") or 0
    if score <= 0:
        return False
    if (f.get("adx") or 0) < 20:
        return False
    if (f.get("rs_vs_tasi") or 0) < 1.0:
        return False
    if (r.get("change_pct") or 0) >= 6.0:        # anti-chasing
        return False
    if (r.get("mtf_aligned_count") or 0) < 1:
        return False
    if len(r.get("signals_active") or []) < 5:
        return False
    return True


def day_score_floor(rows, pct=0.60):
    scores = sorted((r.get("score_raw") or 0) for r in rows if (r.get("score_raw") or 0) > 0)
    if not scores:
        return 0.0
    return scores[min(int(len(scores) * pct), len(scores) - 1)]


def pick_day(rows, regime_ok=True, max_ext=None, require_regime=False):
    if require_regime and not regime_ok:
        return []
    floor = day_score_floor(rows)
    cands = [r for r in rows if passes_base(r) and (r.get("score_raw") or 0) >= floor]
    if max_ext is not None:
        cands = [r for r in cands
                 if (r.get("features", {}).get("dist_from_sma20_pct") or 0) <= max_ext]
    cands.sort(key=lambda r: r.get("score_raw") or 0, reverse=True)
    return cands[:TOP_N]


def fwd(r):
    return r.get("next_3d_close_pct")


def summarize(picks, label):
    rets = [fwd(p) for p in picks if fwd(p) is not None]
    if not rets:
        print(f"  {label:<34} n=  0  (لا صفقات)")
        return {"label": label, "n": 0, "avg": None, "wr": None, "pos": None}
    avg = st.mean(rets)
    wr = 100 * sum(1 for x in rets if x >= TARGET) / len(rets)
    pos = 100 * sum(1 for x in rets if x > 0) / len(rets)
    print(f"  {label:<34} n={len(rets):>3}  avgFwd3={avg:+.2f}%  P(+2%)={wr:>3.0f}%  P(>0)={pos:>3.0f}%")
    return {"label": label, "n": len(rets), "avg": round(avg, 2),
            "wr": round(wr), "pos": round(pos)}


def _save_outputs(meta, rows, regime):
    """يحفظ النتائج كـ JSON + صفحة HTML تُفتح مثل لوحة التحكم."""
    import os
    os.makedirs("public", exist_ok=True)
    payload = {"meta": meta, "strategies": rows,
               "regime": [{"date": d, "trend": round(v, 2)} for d, v in regime.items()]}
    try:
        with open("tadawul_data/backtest_results.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"     ⚠️ تعذّر حفظ JSON: {e}")

    def fmt(v, suf="%"):
        return "—" if v is None else f"{v:+.2f}{suf}" if suf == "%" and isinstance(v, float) else f"{v}{suf}"

    tr = ""
    for r in rows:
        avg = "—" if r["avg"] is None else f"{r['avg']:+.2f}%"
        wr = "—" if r["wr"] is None else f"{r['wr']}%"
        pos = "—" if r["pos"] is None else f"{r['pos']}%"
        hot = "background:#e8f5e9" if (r["avg"] or -9) > 0 else ""
        tr += (f"<tr style='{hot}'><td style='text-align:right'>{r['label']}</td>"
               f"<td>{r['n']}</td><td>{avg}</td><td>{wr}</td><td>{pos}</td></tr>")

    reg = "".join(
        f"<span style='display:inline-block;margin:2px;padding:3px 8px;border-radius:6px;"
        f"background:{'#e8f5e9' if v>=0 else '#ffebee'}'>{d[5:]}: {v:+.2f}</span>"
        for d, v in regime.items())

    html = f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest الاستراتيجيات</title>
<style>body{{font-family:system-ui,Arial;max-width:760px;margin:18px auto;padding:0 14px;color:#1a1a1a}}
h2{{margin:.2em 0}}table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:15px}}
th,td{{border:1px solid #ddd;padding:8px;text-align:center}}th{{background:#fafafa}}
.warn{{background:#fff8e1;border:1px solid #ffe082;padding:10px;border-radius:8px;font-size:14px}}
.muted{{color:#777;font-size:13px}}</style>
<h2>📊 مقارنة استراتيجيات الدخول/الفلترة</h2>
<p class="muted">آخر تحديث: {meta['generated_at']} | أيام مُقيّمة: {meta['days']} | النافذة: {meta['window']}</p>
<div class="warn">⚠️ النافذة قصيرة ({meta['days']} أيام، غالبها نظام سوقي واحد). النتائج إرشادية لا قاطعة —
تتحسّن دقتها تلقائياً كلما تراكمت بيانات الأيام. الأخضر = متوسط عائد موجب.</div>
<table><thead><tr><th>الاستراتيجية</th><th>عدد الصفقات</th><th>متوسط عائد 3 أيام</th><th>إصابة +2%</th><th>نسبة الرابحة</th></tr></thead>
<tbody>{tr}</tbody></table>
<p class="muted">خط الأساس (كل السوق): متوسط {meta['baseline_avg']:+.2f}% — أي استراتيجية فوقه تتفوّق على السوق.</p>
<h3>اتجاه السوق لكل يوم (trailing 3d)</h3><div>{reg}</div>
<p class="muted">⚠️ ليست نصيحة مالية — أداة تقييم داخلية.</p></html>"""
    try:
        with open("public/backtest.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("     ✓ نتائج backtest → public/backtest.html")
    except Exception as e:
        print(f"     ⚠️ تعذّر حفظ HTML: {e}")


def run():
    from datetime import datetime
    days = load_days()
    days = {d: r for d, r in days.items()
            if any(x.get("next_3d_close_pct") is not None for x in r)}
    regime, daily_mkt = market_regime(load_days())

    if not days:
        print("  ⚠️ backtest: لا توجد snapshots مُقيّمة بعد (تحتاج ≥3 أيام لاحقة).")
        return

    print("=" * 70)
    print(f"Backtest الاستراتيجيات | أيام قابلة للتقييم: {len(days)}")
    print(f"النافذة: {min(days)} → {max(days)}  (⚠️ قصيرة - إرشادية لا قاطعة)")
    print("=" * 70)

    allrows = [r for rs in days.values() for r in rs if r.get("next_3d_close_pct") is not None]
    base = summarize(allrows, "خط الأساس (كل السوق)")
    print()

    A, B, C, D = [], [], [], []
    for d, rows in days.items():
        ok = regime[d] >= 0
        A += pick_day(rows)
        B += pick_day(rows, regime_ok=ok, require_regime=True)
        C += pick_day(rows, max_ext=8.0)
        D += pick_day(rows, regime_ok=ok, require_regime=True, max_ext=8.0)

    results = [
        summarize(A, "A) V9.2.4 الأساس"),
        summarize(B, "B) A + فلتر النظام السوقي"),
        summarize(C, "C) A + تقييد التمدّد ≤8% فوق SMA20"),
        summarize(D, "D) B + C معاً"),
    ]
    print()
    print("اتجاه السوق لكل يوم (trailing 3d، موجب=صاعد):")
    for d in days:
        print(f"  {d}: {regime[d]:+.2f}  ({'صاعد' if regime[d] >= 0 else 'هابط'})")

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "days": len(days),
        "window": f"{min(days)} → {max(days)}",
        "baseline_avg": base["avg"] if base["avg"] is not None else 0.0,
    }
    _save_outputs(meta, results, {d: regime[d] for d in days})


if __name__ == "__main__":
    run()
