# -*- coding: utf-8 -*-
"""
rebuild_ml_from_universe.py — V9.3 (الإصلاح الجوهري لحلقة التعلم)
==================================================================
المشكلة المُكتشفة (تشريح 2026-06-11):
    1. ml_dataset.csv كان يتغذى من مصدرين بمخططي أعمدة مختلفين:
       - scanner_v9.evaluate_yesterday (23 عمود، picks فقط = survivorship bias)
       - paper_trading_engine._append_to_ml_dataset (22 عمود بترتيب مختلف!)
       النتيجة: 39 صفاً قيمة hit فيها نصوص ("Stop Hit"...) → تدريب XGBoost
       ينهار يومياً بصمت → ml_metrics مجمّدة منذ 2026-05-18.
    2. الأدهى: universe_snapshots تحتوي آلاف الصفوف الموسومة الخالية من
       survivorship (التي بُنيت خصيصاً لهذا الغرض) ولا يستخدمها التدريب أبداً.

الحل (هذا الملف):
    إعادة بناء ml_dataset.csv **بالكامل وبشكل حتمي (deterministic)** من
    universe_snapshots المُقيّمة في كل تشغيلة:
      - مصدر واحد للحقيقة → استحالة فساد المخطط مستقبلاً
      - بيانات خالية من survivorship (كل ~194 سهم يومياً، لا الـ picks فقط)
      - ينمو ~194 صف موسوم/يوم تلقائياً

    صفقات الـ paper trading تذهب الآن لملف منفصل paper_outcomes.csv
    (انظر paper_trading_engine) — تُستخدم لتقييم الاستراتيجية لا لتدريب ML.

يُستدعى من run_all.py في الخطوة [0] مباشرة بعد evaluate_universe.run().
"""
import json
import glob
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
SNAPSHOTS_DIR = BASE / "universe_snapshots"
F_DATASET = BASE / "ml_dataset.csv"

# نفس FEATURES في ml_engine.py — مصدر واحد للأسماء
try:
    from ml_engine import FEATURES
except ImportError:
    FEATURES = [
        "rsi", "stoch_rsi_k", "macd_hist", "bb_pct", "bb_width",
        "adx", "di_plus", "di_minus", "mfi", "cmf", "fib_pos",
        "supertrend_dir", "weekly_trend", "rs_vs_tasi",
        "vol_ratio", "vwap_diff_pct",
        "dist_from_sma20_pct", "dist_from_sma50_pct", "obv_vs_ma_pct",
    ]

COLUMNS = FEATURES + ["hit", "ticker", "date", "was_picked"]


def _iter_labeled_rows():
    """يمر على كل صفوف snapshots التي اكتمل تقييمها (hit ليست None)."""
    for path in sorted(glob.glob(str(SNAPSHOTS_DIR / "snapshot_*.jsonl"))):
        date = Path(path).name.replace("snapshot_", "").replace(".jsonl", "")
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("hit") is None:
                continue  # لم تكتمل نافذته بعد
            feats = r.get("features") or {}
            row = {}
            ok = True
            for f in FEATURES:
                v = feats.get(f)
                if v is None:
                    ok = False
                    break
                try:
                    row[f] = float(v)
                except (TypeError, ValueError):
                    ok = False
                    break
            if not ok:
                continue
            try:
                row["hit"] = int(r["hit"])
            except (TypeError, ValueError):
                continue
            row["ticker"] = r.get("ticker", "")
            row["date"] = r.get("date", date)
            row["was_picked"] = 1 if r.get("was_picked") else 0
            yield row


def rebuild() -> dict:
    """يعيد كتابة ml_dataset.csv كاملاً من الـ snapshots (atomic)."""
    import csv

    rows = list(_iter_labeled_rows())
    if not rows:
        log.warning("rebuild_ml_from_universe: لا صفوف موسومة — لم يُكتب شيء")
        return {"rows": 0, "written": False}

    # ترتيب زمني (ضروري لـ walk-forward CV)
    rows.sort(key=lambda r: (r["date"], r["ticker"]))

    tmp = str(F_DATASET) + ".tmp"
    F_DATASET.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    Path(tmp).replace(F_DATASET)

    hit_rate = 100.0 * sum(r["hit"] for r in rows) / len(rows)
    summary = {
        "rows": len(rows),
        "written": True,
        "base_hit_rate_pct": round(hit_rate, 1),
        "date_range": f"{rows[0]['date']} → {rows[-1]['date']}",
        "rebuilt_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    log.info(f"rebuild_ml_from_universe: {summary}")
    return summary


def run():
    """نقطة دخول run_all.py."""
    print("  🧱 إعادة بناء ml_dataset من universe snapshots (خالٍ من survivorship)...")
    s = rebuild()
    if s.get("written"):
        print(f"     ✓ {s['rows']} صف موسوم | hit-rate أساس: {s['base_hit_rate_pct']}% "
              f"| {s['date_range']}")
    else:
        print("     ⚠️ لا صفوف موسومة بعد")
    return s


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
