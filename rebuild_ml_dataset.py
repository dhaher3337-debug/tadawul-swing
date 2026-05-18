#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_ml_dataset.py — أداة إصلاح Bug 2 (ML overfit)
========================================================
يُعيد بناء ml_dataset.csv من paper_trades.json الفعلية.

السبب:
    التحليل كشف أن ml_dataset.csv لم يكن يتحدّث (samples=242 ثابتة 6 أيام)،
    وأن ML metrics كانت overfit (AUC=0.937 لكن WR الفعلي=36%).
    
الحل:
    1. حذف ml_dataset.csv القديم (نُحفظه احتياطياً)
    2. بناء dataset جديد من paper_trades.json (closed trades فقط)
    3. كل closed trade يصبح صفاً واحداً مع features + hit (1 if profit else 0)
    4. تشغيل تدريب ML جديد مع walk-forward validation

كيف يُستخدم:
    python3 rebuild_ml_dataset.py [--keep-old] [--no-train]

✋ يجب تشغيله مرة واحدة فقط بعد رفع الملفات الجديدة!
"""
import json
import csv
import shutil
import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
F_TRADES = BASE / "paper_trades.json"
F_DATASET = BASE / "ml_dataset.csv"
F_DATASET_BACKUP = BASE / f"ml_dataset_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

# الأعمدة المطلوبة في ml_dataset
COLUMNS = [
    "date", "ticker", "sector", "signal_type",
    "score", "mtf_aligned", "rsi", "adx", "mfi", "volume_ratio",
    "atr_at_entry", "power_score", "sector_flow",
    "ml_probability", "expected_value_pct", "risk_reward",
    "mae_pct", "mfe_pct", "days_held", "exit_reason",
    "final_pnl_pct", "hit",
]


def rebuild(keep_old=False):
    """يُعيد بناء ml_dataset.csv من paper_trades.json."""
    
    # 1. التحقق من وجود paper_trades.json
    if not F_TRADES.exists():
        log.error(f"❌ لم يُعثر على {F_TRADES}")
        return False
    
    with open(F_TRADES, "r", encoding="utf-8") as f:
        db = json.load(f)
    
    closed = db.get("closed", [])
    if not closed:
        log.warning("⚠️ لا توجد صفقات مغلقة في paper_trades.json")
        return False
    
    log.info(f"📊 وُجدت {len(closed)} صفقة مغلقة في paper_trades.json")
    
    # 2. backup الـ dataset القديم
    if F_DATASET.exists():
        if keep_old:
            shutil.copy2(F_DATASET, F_DATASET_BACKUP)
            log.info(f"💾 احتفظنا بنسخة احتياطية: {F_DATASET_BACKUP.name}")
        else:
            log.info(f"🗑️ حذف القديم: {F_DATASET}")
            F_DATASET.unlink()
    
    # 3. بناء الصفوف
    rows = []
    skipped = 0
    
    for trade in closed:
        # نتأكد من البيانات الأساسية
        if trade.get("final_pnl_pct") is None:
            skipped += 1
            continue
        
        pnl = trade.get("final_pnl_pct", 0)
        hit = 1 if pnl > 0 else 0
        
        # نحسب days_held إذا غير موجود
        days_held = trade.get("days_held")
        if days_held is None:
            try:
                open_d = datetime.strptime(trade.get("open_date", ""), "%Y-%m-%d")
                close_d = datetime.strptime(trade.get("close_date", ""), "%Y-%m-%d")
                days_held = (close_d - open_d).days
            except Exception:
                days_held = trade.get("days_open", 0)
        
        row = {
            "date": trade.get("open_date"),
            "ticker": trade.get("ticker"),
            "sector": trade.get("sector"),
            "signal_type": trade.get("signal_type"),
            "score": trade.get("score"),
            "mtf_aligned": trade.get("mtf_aligned"),
            "rsi": trade.get("rsi"),
            "adx": trade.get("adx"),
            "mfi": trade.get("mfi"),
            "volume_ratio": trade.get("volume_ratio"),
            "atr_at_entry": trade.get("atr_at_entry"),
            "power_score": trade.get("power_score"),
            "sector_flow": trade.get("sector_flow"),
            "ml_probability": trade.get("ml_probability"),
            "expected_value_pct": trade.get("expected_value_pct"),
            "risk_reward": trade.get("risk_reward"),
            "mae_pct": trade.get("mae_pct"),
            "mfe_pct": trade.get("mfe_pct"),
            "days_held": days_held,
            "exit_reason": trade.get("exit_reason"),
            "final_pnl_pct": pnl,
            "hit": hit,
        }
        rows.append(row)
    
    log.info(f"✅ بُنيت {len(rows)} صف (تم تجاوز {skipped})")
    
    # 4. كتابة CSV
    F_DATASET.parent.mkdir(parents=True, exist_ok=True)
    with open(F_DATASET, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    
    log.info(f"💾 كُتب: {F_DATASET}")
    
    # 5. إحصائيات سريعة
    wins = sum(1 for r in rows if r["hit"] == 1)
    losses = sum(1 for r in rows if r["hit"] == 0)
    log.info(f"\n📈 إحصائيات الـ dataset الجديد:")
    log.info(f"   إجمالي الصفوف: {len(rows)}")
    log.info(f"   ✅ Wins (hit=1): {wins} ({wins/len(rows)*100:.1f}%)")
    log.info(f"   ❌ Losses (hit=0): {losses} ({losses/len(rows)*100:.1f}%)")
    
    return True


def trigger_retrain():
    """يُشغّل تدريب ML الجديد."""
    log.info("\n🤖 تشغيل تدريب ML V9.2.3...")
    try:
        from ml_engine import train_model
        # min_samples=20 لأن لدينا عينة صغيرة بعد الـ rebuild
        result = train_model(min_samples=20)
        log.info(f"📊 نتيجة التدريب: {result.get('status')}")
        
        if result.get("trustability"):
            trust = result["trustability"]
            log.info(f"   ثقة النموذج: {trust.get('trust_level')}")
            log.info(f"   استخدام probability: {trust.get('use_ml_probability')}")
            for reason in trust.get("reasons", []):
                log.info(f"   - {reason}")
        
        if result.get("metrics", {}).get("walk_forward"):
            wf = result["metrics"]["walk_forward"]
            log.info(f"\n📉 Walk-Forward Results:")
            log.info(f"   AUC (out-of-sample): {wf.get('avg_roc_auc_out')}")
            log.info(f"   AUC (in-sample): {wf.get('avg_roc_auc_in')}")
            log.info(f"   Overfit gap: {wf.get('overfit_gap')}")
            log.info(f"   Is overfit: {wf.get('is_overfit')}")
        
        return True
    except ImportError as e:
        log.error(f"❌ لم نتمكن من استيراد ml_engine: {e}")
        return False
    except Exception as e:
        log.error(f"❌ فشل التدريب: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="إعادة بناء ml_dataset.csv من paper_trades.json")
    parser.add_argument("--keep-old", action="store_true",
                        help="احتفظ بنسخة احتياطية من الـ dataset القديم")
    parser.add_argument("--no-train", action="store_true",
                        help="لا تُشغّل تدريب ML بعد إعادة البناء")
    args = parser.parse_args()
    
    print(f"\n{'='*60}")
    print("🔧 rebuild_ml_dataset.py — V9.2.3 إصلاح Bug 2")
    print(f"{'='*60}\n")
    
    success = rebuild(keep_old=args.keep_old)
    
    if success and not args.no_train:
        trigger_retrain()
    
    print(f"\n{'='*60}")
    print("✅ انتهى" if success else "❌ فشل")
    print(f"{'='*60}\n")
    
    sys.exit(0 if success else 1)
