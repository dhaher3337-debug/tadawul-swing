# -*- coding: utf-8 -*-
"""
evaluate_universe.py — V9.2.4 (الحلقة المفقودة)
================================================
الغرض:
    إغلاق حلقة التعلّم. universe_snapshots.py يحفظ صفوفاً بـ
    next_3d_* = None ("تُملأ لاحقاً بواسطة evaluate_universe.py") — لكن هذا
    الملف لم يكن موجوداً ولا يُستدعى من run_all.py مطلقاً. النتيجة:
    آلاف الصفوف بلا outcome، فالـ ML لا يحصل على labels خالية من
    survivorship، و"التعلّم اليومي" لا يحدث فعلياً.

ماذا يفعل:
    لكل snapshot مضى عليه ≥ horizon أيام تداول وما زال outcome=None،
    يحسب من الأسعار اللاحقة:
        next_3d_max_pct, next_3d_min_pct, next_3d_close_pct, hit
    ثم يُعيد كتابة ملف الـ snapshot (atomic) مع الـ labels.

مصدر الأسعار (بالترتيب):
    1. price_fn مُمرَّرة من المُتصل (production: yfinance عبر data_sources)
    2. fallback offline: لوحة أسعار مبنية من إغلاقات snapshots نفسها
       (تسمح بالـ backfill بلا إنترنت — مفيد للاختبار والتعويض الرجعي)

تعريف hit (متوافق مع schema في universe_snapshots.py):
    hit = 1 إذا بلغ السعر +TARGET_PCT خلال النافذة دون أن يضرب -STOP_PCT أولاً
        (تقريب: نملك إغلاقات يومية لا مسار intraday، فنستخدم max/min اليومي)
    hit = 0 خلاف ذلك
"""
import json
import glob
import logging
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
SNAPSHOTS_DIR = BASE / "universe_snapshots"

HORIZON_DAYS = 3        # أفق التقييم (أيام تداول)
TARGET_PCT = 1.5        # هدف النجاح
STOP_PCT = -2.0         # حد الفشل


def _list_snapshot_files():
    return sorted(glob.glob(str(SNAPSHOTS_DIR / "snapshot_*.jsonl")))


def _date_of(path: str) -> str:
    return Path(path).name.replace("snapshot_", "").replace(".jsonl", "")


def _read_rows(path: str) -> list:
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def _build_offline_price_panel():
    """لوحة أسعار {ticker: {date: close}} من إغلاقات كل الـ snapshots."""
    panel = {}
    dates = []
    for path in _list_snapshot_files():
        d = _date_of(path)
        dates.append(d)
        for r in _read_rows(path):
            t = r.get("ticker")
            c = r.get("close")
            if t and c is not None:
                panel.setdefault(t, {})[d] = c
    return panel, sorted(set(dates))


def _make_offline_price_fn(panel, dates):
    def price_fn(ticker, date):
        return panel.get(ticker, {}).get(date)
    return price_fn, dates


def _forward_outcome(ticker, snap_date, trading_dates, price_fn, horizon):
    """يحسب outcome على نافذة [snap_date+1 .. snap_date+horizon]."""
    if snap_date not in trading_dates:
        return None
    i = trading_dates.index(snap_date)
    window = trading_dates[i + 1: i + 1 + horizon]
    if len(window) < horizon:
        return None  # ما زال pending — لم تكتمل النافذة
    c0 = price_fn(ticker, snap_date)
    if not c0:
        return None
    highs, lows, closes = [], [], []
    for d in window:
        c = price_fn(ticker, d)
        if c is None:
            continue
        ret = 100.0 * (c - c0) / c0
        highs.append(ret); lows.append(ret); closes.append(ret)
    if not closes:
        return None
    max_pct = max(highs)
    min_pct = min(lows)
    close_pct = closes[-1]
    hit = 1 if (max_pct >= TARGET_PCT and min_pct > STOP_PCT) else 0
    return {
        "next_3d_max_pct": round(max_pct, 2),
        "next_3d_min_pct": round(min_pct, 2),
        "next_3d_close_pct": round(close_pct, 2),
        "hit": hit,
    }


def _atomic_rewrite(path: str, rows: list):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    Path(tmp).replace(path)


def evaluate_all(price_fn: Optional[Callable] = None,
                 trading_dates: Optional[list] = None,
                 horizon: int = HORIZON_DAYS) -> dict:
    """
    يقيّم كل الـ snapshots المعلّقة.
    price_fn(ticker, date) -> close أو None. إن لم تُمرَّر نَبني لوحة offline.
    """
    if price_fn is None or trading_dates is None:
        panel, dates = _build_offline_price_panel()
        price_fn, trading_dates = _make_offline_price_fn(panel, dates)

    total_evaluated = 0
    total_pending = 0
    files_updated = 0

    for path in _list_snapshot_files():
        d = _date_of(path)
        rows = _read_rows(path)
        changed = False
        for r in rows:
            if r.get("next_3d_close_pct") is not None:
                continue  # مُقيّم سابقاً
            outcome = _forward_outcome(r["ticker"], d, trading_dates, price_fn, horizon)
            if outcome is None:
                total_pending += 1
                continue
            r.update(outcome)
            r["evaluated_at"] = datetime.now().strftime("%Y-%m-%d")
            total_evaluated += 1
            changed = True
        if changed:
            _atomic_rewrite(path, rows)
            files_updated += 1

    summary = {
        "evaluated": total_evaluated,
        "still_pending": total_pending,
        "files_updated": files_updated,
    }
    log.info(f"evaluate_universe: {summary}")
    return summary


def run(price_fn: Optional[Callable] = None, trading_dates: Optional[list] = None):
    """نقطة الدخول لـ run_all.py."""
    print("  🔁 تقييم universe snapshots (إغلاق حلقة التعلّم)...")
    s = evaluate_all(price_fn, trading_dates)
    print(f"     ✓ تم تقييم {s['evaluated']} صف | معلّق {s['still_pending']} "
          f"| ملفات محدّثة {s['files_updated']}")

    # تقرير مختصر: نسبة hit على المُقيَّم (الأساس الخالي من survivorship)
    panel_rows = []
    for path in _list_snapshot_files():
        for r in _read_rows(path):
            if r.get("hit") is not None:
                panel_rows.append(r)
    if panel_rows:
        hit_rate = 100.0 * sum(r["hit"] for r in panel_rows) / len(panel_rows)
        picked = [r for r in panel_rows if r.get("was_picked")]
        msg = f"     📊 hit-rate الكون: {hit_rate:.0f}% على {len(panel_rows)} صف"
        if picked:
            pr = 100.0 * sum(r["hit"] for r in picked) / len(picked)
            msg += f" | hit-rate المُرشَّحة: {pr:.0f}% على {len(picked)}"
        print(msg)
    return s


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("=" * 60)
    print("evaluate_universe.py — V9.2.4 (إغلاق حلقة التعلّم)")
    print("=" * 60)
    run()
