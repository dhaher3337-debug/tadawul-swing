# -*- coding: utf-8 -*-
"""
market_regime.py — V9.3
========================
يحسب "النظام السوقي" (regime) من عرض السوق الكامل في universe_snapshots:
    متوسط change_pct لكل أسهم الكون على آخر N أيام تداول (trailing).

لماذا الآن (الدليل):
    - backtest خالٍ من survivorship (16 يوم): الاستراتيجية D (regime + extension
      cap) أعطت أعلى نسبة رابحة 58% مقابل 51% للأساس.
    - paper trades: مايو (سوق هابط) متوسط -1.53%/صفقة × 39 صفقة،
      يونيو (سوق صاعد) +1.33%/صفقة × 27.

التصميم: **مُعدِّل ناعم لا بوابة صلبة** — في النظام الهابط لا نوقف التداول
(فنخسر بيانات التعلم وحجم العينة) بل نرفع الانتقائية:
    regime صاعد  (avg3d > +0.30): الوضع الافتراضي (percentile 0.60, top 5)
    regime محايد (-0.30..+0.30):  انتقائية أعلى قليلاً (0.65, top 4)
    regime هابط  (avg3d < -0.30): انتقائية عالية (0.75, top 3)

المخرج: tadawul_data/market_regime.json — يقرؤه rules_filter.run().
"""
import json
import glob
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
SNAPSHOTS_DIR = BASE / "universe_snapshots"
F_REGIME = BASE / "market_regime.json"

TRAILING_DAYS = 3
UP_THRESHOLD = 0.30      # متوسط % يومي للكون
DOWN_THRESHOLD = -0.30

# جداول التكييف (يقرؤها rules_filter)
REGIME_PROFILES = {
    "up":      {"score_percentile": 0.60, "top_n": 5},
    "neutral": {"score_percentile": 0.65, "top_n": 4},
    "down":    {"score_percentile": 0.75, "top_n": 3},
}


def _daily_breadth():
    """[(date, avg_change_pct, advancers_pct), ...] مرتبة زمنياً."""
    out = []
    for path in sorted(glob.glob(str(SNAPSHOTS_DIR / "snapshot_*.jsonl"))):
        date = Path(path).name.replace("snapshot_", "").replace(".jsonl", "")
        changes = []
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = r.get("change_pct")
            if c is not None:
                try:
                    changes.append(float(c))
                except (TypeError, ValueError):
                    pass
        if changes:
            avg = sum(changes) / len(changes)
            adv = 100.0 * sum(1 for c in changes if c > 0) / len(changes)
            out.append((date, avg, adv))
    return out


def compute(trailing_days: int = TRAILING_DAYS) -> dict:
    breadth = _daily_breadth()
    if not breadth:
        return {"regime": "neutral", "reason": "لا snapshots", **REGIME_PROFILES["neutral"]}

    window = breadth[-trailing_days:]
    avg3d = sum(b[1] for b in window) / len(window)
    adv3d = sum(b[2] for b in window) / len(window)

    if avg3d > UP_THRESHOLD:
        regime = "up"
    elif avg3d < DOWN_THRESHOLD:
        regime = "down"
    else:
        regime = "neutral"

    result = {
        "regime": regime,
        "trailing_days": len(window),
        "avg_change_pct": round(avg3d, 3),
        "advancers_pct": round(adv3d, 1),
        "as_of": window[-1][0],
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        **REGIME_PROFILES[regime],
    }
    return result


def run() -> dict:
    """يحسب ويحفظ — يُستدعى من run_all قبل الفلترة."""
    r = compute()
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        with open(F_REGIME, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"market_regime: فشل الحفظ ({e})")
    label = {"up": "صاعد 🟢", "down": "هابط 🔴", "neutral": "محايد 🟡"}[r["regime"]]
    print(f"  🌡️ النظام السوقي: {label} (متوسط {r.get('trailing_days', '?')} أيام: "
          f"{r.get('avg_change_pct', '?')}% | متقدمون: {r.get('advancers_pct', '?')}%) "
          f"→ percentile={r['score_percentile']}, top_n={r['top_n']}")
    return r


def load() -> dict:
    """يقرأ آخر regime محفوظ (للاستخدام في rules_filter)."""
    try:
        with open(F_REGIME, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"regime": "neutral", **REGIME_PROFILES["neutral"]}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
