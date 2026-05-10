# -*- coding: utf-8 -*-
"""
Universe Snapshot System — V9.2 Foundation Layer
==================================================
الهدف الاستراتيجي:
    حفظ snapshot يومي لكل سهم في الـ universe (130 سهم) — وليس فقط المرشحين.
    هذا يبني data lake نظيف يكون أساساً دائماً لكل ميزات V9.2 المستقبلية:
      - ML training (negative class من المُفوَّتين)
      - Stock DNA Profile
      - Inter-Stock Correlation
      - Sector Rotation Detection
      - Expected Move Calibration

المبدأ المعماري:
    افصل "تسجيل الحقيقة" عن "اتخاذ القرار". هذه الوحدة تُسجّل فقط، لا تقرر.

تصميم الـ Pilot Phase (الطبقة 1):
    - وحدة مستقلة تماماً، لا تكسر شيئاً
    - تُستدعى مرة واحدة في نهاية scan_tasi()
    - تكتب JSONL يومي (سهل القراءة، سهل الإلحاق، آمن من الفساد الجزئي)
    - بعد ~7 أيام من البيانات الموثوقة، نبني الطبقات 2-5 فوقها

تصميم الملف:
    tadawul_data/universe_snapshots/snapshot_YYYY-MM-DD.jsonl
    صف واحد لكل سهم، حقول ثابتة (schema-stable for downstream ML)

ملاحظة قاتلة منعت في V9.1:
    لا تعتمد على dict ordering عبر الـ Python versions. كل صف يحفظ
    schema_version لتمكين migration نظيف لاحقاً.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════
# الثوابت
# ════════════════════════════════════════════════
BASE = Path("tadawul_data")
SNAPSHOTS_DIR = BASE / "universe_snapshots"

# schema_version: ارفعه عند تغيير شكل الـ snapshot
# downstream code يجب أن يفلتر على schema_version compatible معه
SCHEMA_VERSION = "1.0"

# الحقول التي نريدها مستقرة للأبد (downstream ML سيعتمد عليها)
# لا تحذف حقلاً منها بعد الـ deployment - فقط أضف جديدة
FEATURE_FIELDS = [
    "rsi", "stoch_rsi_k", "macd_hist", "bb_pct", "bb_width",
    "adx", "di_plus", "di_minus",
    "mfi", "cmf", "fib_pos",
    "supertrend_dir", "weekly_trend", "rs_vs_tasi",
    "vol_ratio", "vwap_diff_pct",
    "dist_from_sma20_pct", "dist_from_sma50_pct",
    "obv_vs_ma_pct",
]


def _ensure_dir():
    """إنشاء آمن للمجلد (يتجنب FileExistsError race condition)."""
    try:
        if not SNAPSHOTS_DIR.exists():
            SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass


def _safe_float(v, default=0.0):
    """تحويل آمن لـ float - يتعامل مع None, NaN, strings."""
    if v is None:
        return default
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _build_snapshot_row(
    code: str,
    sector: str,
    last_close: float,
    chg_pct: float,
    features: dict,
    signals_active: list,
    score_raw: float,
    base_score: float,
    ml_prob: Optional[float],
    was_picked: bool,
    pick_rank: Optional[int],
    mtf_info: dict,
    news_info: dict,
    earnings_info: dict,
    today_str: str,
) -> dict:
    """
    يبني صف snapshot لسهم واحد.
    
    Args:
        code: ticker بدون .SR (مثلاً "1120")
        sector: اسم القطاع
        last_close: سعر الإغلاق الأخير
        chg_pct: % التغير اليومي
        features: dict من extract_features_from_snapshot
        signals_active: قائمة الإشارات النشطة (مثل ["rsi", "macd"])
        score_raw: النقاط النهائية بعد multipliers
        base_score: النقاط قبل multipliers
        ml_prob: احتمال ML (قد يكون None)
        was_picked: هل السهم في top picks؟
        pick_rank: ترتيبه في picks (None لو لم يُختر)
        mtf_info, news_info, earnings_info: من V9.1
        today_str: تاريخ اليوم YYYY-MM-DD
    
    Returns:
        dict بـ schema ثابت قابل للـ JSONL serialization
    
    🔴 critical: outcome fields (next_3d_*, hit) تبقى None هنا.
        تُملأ لاحقاً بواسطة evaluate_universe.py بعد 3 أيام.
    """
    # تنظيف features - تأكد من أن كل القيم numeric
    clean_features = {}
    for fname in FEATURE_FIELDS:
        clean_features[fname] = _safe_float(features.get(fname))
    
    return {
        # ─── Metadata (لا تتغير) ───
        "schema_version": SCHEMA_VERSION,
        "date": today_str,
        "ticker": code,
        "sector": sector,
        
        # ─── Price snapshot ───
        "close": round(_safe_float(last_close), 4),
        "change_pct": round(_safe_float(chg_pct), 3),
        
        # ─── Features (input للـ ML) ───
        "features": clean_features,
        
        # ─── Signal activations (لتحليل signal accuracy على universe كامل) ───
        "signals_active": list(signals_active) if signals_active else [],
        
        # ─── System decision (لتحليل bias لاحقاً) ───
        "score_raw": round(_safe_float(score_raw), 3),
        "base_score": round(_safe_float(base_score), 3),
        "ml_prob": round(_safe_float(ml_prob), 4) if ml_prob is not None else None,
        "was_picked": bool(was_picked),
        "pick_rank": int(pick_rank) if pick_rank is not None else None,
        
        # ─── V9.1 multipliers (للتدقيق لاحقاً) ───
        "mtf_multiplier": round(_safe_float(mtf_info.get("mtf_multiplier", 1.0)), 3),
        "mtf_aligned_count": int(mtf_info.get("aligned_count", 0)),
        "news_multiplier": round(_safe_float(news_info.get("multiplier", 1.0)), 3),
        "news_label": str(news_info.get("label", "no_news")),
        "earnings_multiplier": round(_safe_float(earnings_info.get("multiplier", 1.0)), 3),
        
        # ─── Outcome fields (تُملأ لاحقاً) ───
        # ⚠️ لا تحذف هذه الحقول - downstream code يعتمد على وجودها
        "next_3d_max_pct": None,
        "next_3d_min_pct": None,
        "next_3d_close_pct": None,
        "hit": None,                 # 1 = +1.5% خلال 3 أيام بدون ضرب -2%, 0 = فشل, None = pending
        "evaluated_at": None,        # تاريخ التقييم
    }


def save_universe_snapshot(rows: list, today_str: Optional[str] = None) -> dict:
    """
    🎯 الدالة الرئيسية - تُستدعى من scanner_v9.scan_tasi() بعد بناء candidates.
    
    تحفظ snapshot لـ كل الأسهم التي تم سكانها (وليس فقط المرشحين).
    
    Args:
        rows: قائمة dicts من _build_snapshot_row
        today_str: تاريخ اليوم (default: اليوم)
    
    Returns:
        dict {
            "status": "ok" | "error",
            "rows_saved": int,
            "file_path": str,
            "warnings": list,
        }
    
    ضمانات السلامة:
        1. atomic write (نكتب لـ .tmp ثم rename)
        2. لا يحذف بيانات اليوم نفسه إذا تم التشغيل مرتين -
           بدلاً من ذلك، يستبدل الملف كاملاً (idempotent)
        3. لا يرفع exception للـ caller (يلقم خطأ في log فقط)
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    warnings = []
    
    if not rows:
        warnings.append("rows فارغة - لم يتم حفظ أي شيء")
        log.warning("save_universe_snapshot: rows فارغة")
        return {
            "status": "error",
            "rows_saved": 0,
            "file_path": None,
            "warnings": warnings,
        }
    
    _ensure_dir()
    
    file_path = SNAPSHOTS_DIR / f"snapshot_{today_str}.jsonl"
    tmp_path = SNAPSHOTS_DIR / f"snapshot_{today_str}.jsonl.tmp"
    
    # تحقق من سلامة كل صف قبل الكتابة
    valid_rows = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            warnings.append(f"row #{i} ليس dict - تجاهل")
            continue
        if "ticker" not in row or "date" not in row:
            warnings.append(f"row #{i} ينقصه ticker أو date - تجاهل")
            continue
        valid_rows.append(row)
    
    if not valid_rows:
        log.error("save_universe_snapshot: لا توجد صفوف صحيحة")
        return {
            "status": "error",
            "rows_saved": 0,
            "file_path": str(file_path),
            "warnings": warnings,
        }
    
    # كشف صفوف مكررة لنفس السهم في نفس اليوم (يجب ألا يحدث، لكن نحمي)
    seen_tickers = set()
    deduped_rows = []
    for row in valid_rows:
        key = row["ticker"]
        if key in seen_tickers:
            warnings.append(f"ticker مكرر {key} - تجاهل النسخة الثانية")
            continue
        seen_tickers.add(key)
        deduped_rows.append(row)
    
    # Atomic write: tmp → rename
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            for row in deduped_rows:
                # ensure_ascii=False للنص العربي (sector name)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        
        # rename ذرّي على نظام الملفات
        tmp_path.replace(file_path)
        
        log.info(f"Universe snapshot: {len(deduped_rows)} صف → {file_path.name}")
        
        return {
            "status": "ok",
            "rows_saved": len(deduped_rows),
            "file_path": str(file_path),
            "warnings": warnings,
        }
    
    except Exception as e:
        log.error(f"save_universe_snapshot fail: {e}")
        # تنظيف tmp إذا فشل
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        warnings.append(f"كتابة الملف فشلت: {e}")
        return {
            "status": "error",
            "rows_saved": 0,
            "file_path": str(file_path),
            "warnings": warnings,
        }


def load_universe_snapshot(date_str: str) -> list:
    """
    تحميل snapshot ليوم معين.
    
    Args:
        date_str: YYYY-MM-DD
    
    Returns:
        list من dicts، أو [] إذا الملف غير موجود
    """
    file_path = SNAPSHOTS_DIR / f"snapshot_{date_str}.jsonl"
    if not file_path.exists():
        return []
    
    rows = []
    try:
        with open(file_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    log.warning(f"snapshot {date_str} سطر {line_num} فاسد: {e}")
                    continue
    except Exception as e:
        log.error(f"load_universe_snapshot {date_str} fail: {e}")
        return []
    
    return rows


def list_snapshot_dates() -> list:
    """قائمة بكل التواريخ التي لها snapshot، مرتبة تنازلياً."""
    if not SNAPSHOTS_DIR.exists():
        return []
    
    dates = []
    for f in SNAPSHOTS_DIR.glob("snapshot_*.jsonl"):
        # استخرج التاريخ من اسم الملف
        name = f.stem  # snapshot_2026-05-10
        if name.startswith("snapshot_"):
            date_part = name[len("snapshot_"):]
            dates.append(date_part)
    
    return sorted(dates, reverse=True)


def get_snapshot_stats() -> dict:
    """
    إحصاءات سريعة عن الـ snapshots المحفوظة.
    مفيد للـ daily reporting و التحقق من سلامة الـ pipeline.
    """
    dates = list_snapshot_dates()
    
    if not dates:
        return {
            "total_days": 0,
            "earliest_date": None,
            "latest_date": None,
            "total_rows": 0,
            "avg_rows_per_day": 0,
            "ready_for_evaluation": False,
        }
    
    total_rows = 0
    rows_per_day = []
    for d in dates:
        rows = load_universe_snapshot(d)
        n = len(rows)
        total_rows += n
        rows_per_day.append(n)
    
    return {
        "total_days": len(dates),
        "earliest_date": dates[-1],
        "latest_date": dates[0],
        "total_rows": total_rows,
        "avg_rows_per_day": round(total_rows / len(dates), 1) if dates else 0,
        "min_rows_in_a_day": min(rows_per_day) if rows_per_day else 0,
        "max_rows_in_a_day": max(rows_per_day) if rows_per_day else 0,
        # مؤشر جاهزية: 7+ أيام = جاهز للطبقة 2 (evaluate_universe)
        "ready_for_evaluation": len(dates) >= 7,
    }


# ════════════════════════════════════════════════
# CLI للفحص اليدوي
# ════════════════════════════════════════════════
if __name__ == "__main__":
    """
    تشغيل: python universe_snapshots.py
    لعرض حالة الـ snapshots المحفوظة.
    """
    print(f"\n{'='*60}")
    print("Universe Snapshots — حالة النظام")
    print(f"{'='*60}\n")
    
    stats = get_snapshot_stats()
    
    if stats["total_days"] == 0:
        print("⚠️ لا توجد snapshots محفوظة بعد.")
        print(f"   مسار التخزين: {SNAPSHOTS_DIR}")
        print("   شغّل scanner_v9.run() لإنتاج أول snapshot.\n")
    else:
        print(f"  📊 إجمالي الأيام:        {stats['total_days']}")
        print(f"  📅 أقدم تاريخ:           {stats['earliest_date']}")
        print(f"  📅 أحدث تاريخ:           {stats['latest_date']}")
        print(f"  📈 إجمالي الصفوف:        {stats['total_rows']:,}")
        print(f"  📈 متوسط صفوف/يوم:       {stats['avg_rows_per_day']}")
        print(f"  📉 أقل صفوف في يوم:      {stats['min_rows_in_a_day']}")
        print(f"  📈 أعلى صفوف في يوم:     {stats['max_rows_in_a_day']}")
        print()
        if stats["ready_for_evaluation"]:
            print("  ✅ جاهز للطبقة 2 (evaluate_universe + Bayesian weights)")
        else:
            needed = 7 - stats["total_days"]
            print(f"  ⏳ نحتاج {needed} يوم إضافي قبل بناء الطبقة 2")
        print()
