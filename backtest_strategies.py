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
        return None
    avg = st.mean(rets)
    wr = 100 * sum(1 for x in rets if x >= TARGET) / len(rets)
    pos = 100 * sum(1 for x in rets if x > 0) / len(rets)
    print(f"  {label:<34} n={len(rets):>3}  avgFwd3={avg:+.2f}%  P(+2%)={wr:>3.0f}%  P(>0)={pos:>3.0f}%")
    return avg


def run():
    days = load_days()
    days = {d: r for d, r in days.items()
            if any(x.get("next_3d_close_pct") is not None for x in r)}
    regime, daily_mkt = market_regime(load_days())

    print("=" * 70)
    print(f"Backtest الاستراتيجيات | أيام قابلة للتقييم: {len(days)}")
    print(f"النافذة: {min(days)} → {max(days)}  (⚠️ قصيرة - إرشادية لا قاطعة)")
    print("=" * 70)

    # خط الأساس: كل الكون
    allrows = [r for rs in days.values() for r in rs if r.get("next_3d_close_pct") is not None]
    summarize(allrows, "خط الأساس (كل السوق)")
    print()

    A, B, C, D = [], [], [], []
    for d, rows in days.items():
        ok = regime[d] >= 0
        A += pick_day(rows)
        B += pick_day(rows, regime_ok=ok, require_regime=True)
        C += pick_day(rows, max_ext=8.0)
        D += pick_day(rows, regime_ok=ok, require_regime=True, max_ext=8.0)

    summarize(A, "A) V9.2.4 الأساس")
    summarize(B, "B) A + فلتر النظام السوقي")
    summarize(C, "C) A + تقييد التمدد (≤8% فوق SMA20)")
    summarize(D, "D) B + C معاً")
    print()
    print("اتجاه السوق لكل يوم (trailing 3d، موجب=صاعد):")
    for d in days:
        print(f"  {d}: {regime[d]:+.2f}  ({'صاعد' if regime[d] >= 0 else 'هابط'})")


if __name__ == "__main__":
    run()
