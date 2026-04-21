# -*- coding: utf-8 -*-
"""
المنسق الرئيسي — V9
==========================
يشغّل بالتسلسل:
  1. scanner_v9  — مسح + تقييم + ML + ارتباطات
  2. ai_analyst_v9  — تحليل Claude Opus 4.7
  3. build_reports_v9  — بناء HTML
  4. أرشفة الأوزان (weights_history)

يُشغَّل عبر GitHub Actions يومياً الساعة 5 صباحاً سعودي.
"""
import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path

import scanner_v9
import ai_analyst_v9
import build_reports_v9

BASE = Path("tadawul_data")
WEIGHTS_HIST = Path("weights_history")
try:
    if not WEIGHTS_HIST.exists():
        WEIGHTS_HIST.mkdir(parents=True, exist_ok=True)
except FileExistsError:
    pass


def archive_weights():
    """أرشفة ملف الأوزان اليومي + سجل تراكمي."""
    weights_file = BASE / "tasi_weights.json"
    if not weights_file.exists():
        return

    today = datetime.now().strftime("%Y-%m-%d")
    # نسخة يومية
    daily_copy = WEIGHTS_HIST / f"weights_{today}.json"
    shutil.copy(weights_file, daily_copy)

    # سجل تراكمي
    log_file = WEIGHTS_HIST / "history.jsonl"
    with open(weights_file, encoding="utf-8") as f:
        weights = json.load(f)

    entry = {"date": today, "weights": weights}
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def archive_ml_metrics():
    """نسخة يومية من مقاييس ML لمتابعة التحسن عبر الزمن."""
    metrics_file = BASE / "ml_metrics.json"
    if not metrics_file.exists():
        return
    today = datetime.now().strftime("%Y-%m-%d")
    dest = WEIGHTS_HIST / f"ml_metrics_{today}.json"
    shutil.copy(metrics_file, dest)


def main():
    start = datetime.now()
    print(f"\n{'═'*55}")
    print(f"  🚀 ماسح تداول V9 — {start:%Y-%m-%d %H:%M:%S}")
    print(f"{'═'*55}")

    # 1. المسح + التقييم + ML
    print("\n[1/4] 🔍 المسح الفني + تقييم الأمس + تدريب ML")
    print("─" * 55)
    try:
        scanner_v9.run()
    except Exception as e:
        print(f"  ❌ فشل المسح: {e}")
        traceback.print_exc()
        return 1

    # 2. Claude Opus
    print("\n[2/4] 🧠 تحليل Claude Opus 4.7")
    print("─" * 55)
    try:
        ai_analyst_v9.run()
    except Exception as e:
        print(f"  ⚠️ فشل AI (نكمل بدون): {e}")

    # 3. التقرير
    print("\n[3/4] 📄 بناء التقرير")
    print("─" * 55)
    try:
        build_reports_v9.run()
    except Exception as e:
        print(f"  ❌ فشل التقرير: {e}")
        traceback.print_exc()
        return 1

    # 4. الأرشفة
    print("\n[4/4] 💾 أرشفة الأوزان ومقاييس ML")
    print("─" * 55)
    try:
        archive_weights()
        archive_ml_metrics()
        print("  ✓ تمت الأرشفة")
    except Exception as e:
        print(f"  ⚠️ فشل الأرشفة: {e}")

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n{'═'*55}")
    print(f"  ✅ اكتمل في {elapsed:.1f} ثانية")
    print(f"{'═'*55}\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
