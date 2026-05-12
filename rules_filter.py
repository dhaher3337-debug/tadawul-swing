# -*- coding: utf-8 -*-
"""
Rules-Based Filter — V9.2 No-API Mode
========================================
الهدف:
    استبدال Claude/Opus بـ filter حتمي مبني على قواعد صارمة، عندما يكون
    API معطّلاً (سواء عن قصد لتوفير التكلفة، أو عن خطأ).

المبدأ:
    Claude/Opus يحلل سياقاً، يربط متغيرات، ويختار. هذا قيّم لكن:
    - مكلف ($1-3/يوم لـ Opus 4.7)
    - غير حتمي (نفس البيانات قد تنتج picks مختلفة)
    - يعتمد على prompt جودته
    - في وضع ML المنحاز الحالي، يدمج noise

    قواعد بسيطة لكن صارمة (بـ EV>0, RR>1.3, weekly صاعد) تنفذ بدون أي
    من هذه المخاطر، مجاناً، بشفافية كاملة.

متى نعود لـ Claude؟
    بعد بناء universe_snapshots → evaluate_universe → ML نظيف.
    عندها Claude يحصل على بيانات صادقة، فيستحق التكلفة.

تصميم الـ filter:
    1. تطبيق قواعد إقصاء صارمة على كل candidate
    2. ترتيب الـ candidates الناجحين بـ "score مركب" (composite)
    3. اختيار top-N (افتراضياً 7)
    4. تسجيل أسباب القبول/الرفض لكل سهم (شفافية كاملة)

📊 الـ output متوافق مع ai_result.json schema الحالي بحيث run_all.py
   يعمل بدون تعديل.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
F_CANDIDATES = BASE / "tasi_candidates.json"
F_AI_RESULT = BASE / "ai_result.json"
F_FILTER_LOG = BASE / "rules_filter_log.jsonl"


# ════════════════════════════════════════════════
# قواعد الإقصاء (Hard Rules — لا استثناءات)
# ════════════════════════════════════════════════
RULES = {
    # EV >= 0: لا نقبل أبداً سهماً متوقع له خسارة
    "min_ev_pct": 0.0,
    
    # RR >= 1.3: حد أدنى لنسبة المكافأة/المخاطرة
    # 1.3 (وليس 1.5) لأن أسهم Tadawul الصغيرة لها ATR منخفض
    "min_risk_reward": 1.3,
    
    # Score >= 4.0: عتبة جودة فنية
    "min_score": 4.0,
    
    # ADX >= 15: تجنّب الأسواق العرضية تماماً
    "min_adx": 15.0,
    
    # weekly_trend ≠ "هابط": حماية من التداول ضد الاتجاه الكبير
    # استثناء: نقبل "هابط" إذا signals >= 5 (إجماع قوي جداً)
    "block_weekly_haboot": True,
    "weekly_haboot_min_signals": 5,
    
    # MTF: لا نقبل أسهماً MTF ضعيف (أقل من 1 من المتاح)
    # mtf_aligned_count >= 1 من أصل mtf_available
    "min_mtf_aligned": 1,
    
    # عدد الإشارات النشطة: على الأقل 3 لتفادي one-trick ponies
    "min_active_signals": 3,
}


# ════════════════════════════════════════════════
# عوامل الـ Composite Score (لترتيب الناجحين)
# ════════════════════════════════════════════════
SCORE_WEIGHTS = {
    "score": 0.25,           # كان 0.30 - خفّض لإفساح مجال power
    "ev_pct": 0.25,          # كان 0.30 - خفّض لإفساح مجال power
    "risk_reward": 0.13,     # كان 0.15
    "adx": 0.08,             # كان 0.10
    "mtf_alignment": 0.09,   # كان 0.10
    "volume_signal": 0.05,   # نفسه
    "power_score": 0.15,     # 🆕 V9.2.2: Power Classifier (V301)
}
# المجموع: 1.00 ✓


def _safe(v, default=0.0):
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def evaluate_candidate(candidate: dict) -> dict:
    """
    يقيّم candidate واحد ضد القواعد، يُرجع تقريراً مفصّلاً.
    
    Returns:
        {
            "ticker": str,
            "passed": bool,
            "rejections": list of str (أسباب الرفض إن وُجدت),
            "warnings": list of str (تحذيرات لكن مقبول),
            "composite_score": float,
            "components": dict (تفصيل composite_score),
        }
    """
    ticker = candidate.get("ticker", "?")
    rejections = []
    warnings = []
    
    # ─── استخراج البيانات ───
    score = _safe(candidate.get("score"))
    ev_pct = _safe(candidate.get("expected_value_pct"))
    rr = _safe(candidate.get("risk_reward"))
    adx = _safe(candidate.get("adx"))
    weekly = candidate.get("weekly_trend", "")
    signals = candidate.get("signals", []) or []
    mtf_aligned = _safe(candidate.get("mtf_aligned"))
    mtf_available = _safe(candidate.get("mtf_available"))
    volume_ratio = _safe(candidate.get("volume_ratio"))
    
    # ─── القواعد الصارمة ───
    
    # القاعدة 1: EV
    if ev_pct < RULES["min_ev_pct"]:
        rejections.append(f"EV={ev_pct:+.2f}% (< {RULES['min_ev_pct']})")
    
    # القاعدة 2: RR
    if rr < RULES["min_risk_reward"]:
        rejections.append(f"RR=1:{rr} (< 1:{RULES['min_risk_reward']})")
    
    # القاعدة 3: Score
    if score < RULES["min_score"]:
        rejections.append(f"score={score} (< {RULES['min_score']})")
    
    # القاعدة 4: ADX
    # 🔴 استثناء V9.2: ADX قياس متأخر (يحتاج 14 يوماً ليرتفع)
    # السهم في early breakout ستكون ADX منخفضة لكن volume + breakout قويّان
    # الاستثناء: إذا volume_surge >= 2.0× AND breakout signal active، تجاهل ADX
    has_volume_breakout = (
        "volume_surge" in signals and
        "breakout" in signals and
        volume_ratio >= 2.0
    )
    if adx < RULES["min_adx"] and not has_volume_breakout:
        rejections.append(f"ADX={adx:.1f} (< {RULES['min_adx']})")
    elif adx < RULES["min_adx"] and has_volume_breakout:
        warnings.append(
            f"ADX={adx:.1f} منخفض (متأخر) لكن volume {volume_ratio:.1f}× + breakout - "
            f"early breakout مقبول"
        )
    
    # القاعدة 5: weekly trend
    if RULES["block_weekly_haboot"] and weekly == "هابط":
        if len(signals) < RULES["weekly_haboot_min_signals"]:
            rejections.append(
                f"weekly=هابط بدون إجماع كافٍ "
                f"({len(signals)} إشارات < {RULES['weekly_haboot_min_signals']})"
            )
        else:
            warnings.append(
                f"weekly=هابط لكن إجماع {len(signals)} إشارات قوي - مقبول بحذر"
            )
    
    # القاعدة 6: MTF
    if mtf_available > 0 and mtf_aligned < RULES["min_mtf_aligned"]:
        rejections.append(f"MTF aligned={mtf_aligned}/{mtf_available} ضعيف")
    
    # القاعدة 7: Active signals
    if len(signals) < RULES["min_active_signals"]:
        rejections.append(
            f"إشارات={len(signals)} (< {RULES['min_active_signals']})"
        )
    
    # ─── تحذيرات إضافية (لا ترفض، لكن نسجلها) ───
    
    # تحذير: breakout بدون volume = false breakout محتمل
    if "breakout" in signals and volume_ratio < 1.0:
        warnings.append(
            f"⚠️ breakout بدون حجم (vol_ratio={volume_ratio:.1f}× < 1) - "
            f"خطر false breakout"
        )
    
    # تحذير: RSI > 70 = منطقة overbought
    rsi = _safe(candidate.get("rsi"))
    if rsi > 70:
        warnings.append(f"⚠️ RSI={rsi:.0f} في منطقة overbought")
    
    # تحذير: الحجم المنخفض جداً
    if volume_ratio > 0 and volume_ratio < 0.5:
        warnings.append(f"⚠️ حجم منخفض جداً ({volume_ratio:.1f}× من المتوسط)")
    
    # ─── حساب composite score ───
    passed = len(rejections) == 0
    composite_score, components = compute_composite_score(candidate) if passed else (0.0, {})
    
    return {
        "ticker": ticker,
        "passed": passed,
        "rejections": rejections,
        "warnings": warnings,
        "composite_score": composite_score,
        "components": components,
    }


def compute_composite_score(candidate: dict) -> tuple:
    """
    يحسب composite score لترتيب الـ candidates الناجحين.
    
    كل عامل مُطبَّع إلى [0, 1] قبل الجمع المرجَّح.
    
    Returns:
        (composite_score, components_dict)
    """
    components = {}
    
    # 1. Score الفني (مُطبَّع: score=10 → 1.0، score=4 → 0.4)
    score = _safe(candidate.get("score"))
    components["score"] = min(score / 10.0, 1.0)
    
    # 2. EV (مُطبَّع: EV=5% → 1.0، EV=0% → 0.0)
    ev_pct = _safe(candidate.get("expected_value_pct"))
    components["ev_pct"] = min(max(ev_pct / 5.0, 0.0), 1.0)
    
    # 3. RR (مُطبَّع: RR=2.5 → 1.0، RR=1.3 → 0.0)
    rr = _safe(candidate.get("risk_reward"))
    components["risk_reward"] = min(max((rr - 1.3) / (2.5 - 1.3), 0.0), 1.0)
    
    # 4. ADX (مُطبَّع: ADX=40 → 1.0، ADX=15 → 0.0)
    adx = _safe(candidate.get("adx"))
    components["adx"] = min(max((adx - 15) / (40 - 15), 0.0), 1.0)
    
    # 5. MTF alignment (نسبة)
    mtf_aligned = _safe(candidate.get("mtf_aligned"))
    mtf_available = _safe(candidate.get("mtf_available"), 1)
    components["mtf_alignment"] = mtf_aligned / max(mtf_available, 1)
    
    # 6. Volume signal (1.0 إذا vol > 1.5×، 0 إذا < 0.5×)
    volume_ratio = _safe(candidate.get("volume_ratio"))
    components["volume_signal"] = min(max((volume_ratio - 0.5) / (1.5 - 0.5), 0.0), 1.0)
    
    # 7. 🆕 V9.2.2: Power Score من V301 Pine Script
    # مُطبَّع: power=100 → 1.0، power=50 → 0.0 (دون 50 = NONE/WEAK لا يحفّز)
    # السبب: 50 هو الحد الأدنى للتسجيل في power_classifier.py
    power_score = _safe(candidate.get("power_score"))
    if power_score >= 50:
        components["power_score"] = (power_score - 50) / 50.0
    else:
        components["power_score"] = 0.0
    
    # المجموع المرجَّح (7 مكوّنات الآن)
    composite = sum(components[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
    
    return round(composite, 4), components


def filter_candidates(candidates: list, top_n: int = 7) -> dict:
    """
    🎯 الدالة الرئيسية - تأخذ candidates، تُطبّق قواعد، ترجع نتيجة منظمة.
    
    Args:
        candidates: list من scanner_v9
        top_n: عدد الـ picks النهائية (افتراضياً 7)
    
    Returns:
        dict متوافق مع ai_result.json schema:
        {
            "date": str,
            "model": "rules_filter_v1",
            "no_ai": False,  # إنه AI لكن deterministic
            "market_outlook": "محايد" (لأننا لا نحلل ماكرو هنا),
            "picks": list (الناجحون مرتبون),
            "rejected": list (للشفافية),
            "stats": dict,
        }
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    if not candidates:
        return _empty_result(today_str)
    
    # تقييم كل candidate
    evaluations = [evaluate_candidate(c) for c in candidates]
    
    # فصل passed و rejected
    passed_evals = [e for e in evaluations if e["passed"]]
    rejected_evals = [e for e in evaluations if not e["passed"]]
    
    # ترتيب الناجحين بـ composite score
    passed_evals.sort(key=lambda x: -x["composite_score"])
    
    # اختيار top-N
    picks_evals = passed_evals[:top_n]
    
    # بناء picks بـ schema متوافق مع ai_result.json
    candidates_by_ticker = {c["ticker"]: c for c in candidates}
    picks = []
    for eval_result in picks_evals:
        t = eval_result["ticker"]
        full_data = candidates_by_ticker.get(t, {})
        
        # action: شراء قوي إذا composite > 0.7، شراء عادي إذا 0.5-0.7
        composite = eval_result["composite_score"]
        if composite >= 0.65:
            action = "شراء قوي"
        elif composite >= 0.45:
            action = "شراء"
        else:
            action = "مراقبة"
        
        # confidence: مقياس 0-100
        confidence = int(composite * 100)
        
        # reason: استخلاص الأسباب من signals + tag للقواعد
        signals_arabic_map = {
            "rsi": "RSI", "stoch_rsi": "StochRSI", "macd": "MACD",
            "bollinger": "بولنجر", "obv": "OBV", "vwap": "VWAP",
            "volume_surge": "حجم", "sma_cross": "تقاطع ذهبي",
            "breakout": "اختراق", "candle_pattern": "شموع",
            "oil_correlation": "نفط", "adx": "ADX",
            "supertrend": "Supertrend", "ichimoku": "إيشيموكو",
            "mfi": "MFI", "cmf": "CMF", "fibonacci": "فيبو",
            "relative_strength": "قوة نسبية", "weekly_trend": "أسبوعي",
        }
        signals = full_data.get("signals", [])
        signal_names = [signals_arabic_map.get(s, s) for s in signals[:5]]
        reason = f"إجماع {len(signals)} إشارات: " + " + ".join(signal_names)
        
        # 🚀 V9.2.2: إبراز Power Classification في reason
        power_cls = full_data.get("power_classification", "NONE")
        power_score = full_data.get("power_score", 0)
        power_emoji = full_data.get("power_emoji", "")
        
        if power_cls == "ROCKET":
            reason = f"{power_emoji} ROCKET ({power_score}/100) - " + reason
        elif power_cls == "STRONG":
            reason = f"{power_emoji} STRONG ({power_score}/100) - " + reason
        elif power_cls in ("CRASH", "DUMP"):
            reason = f"{power_emoji} {power_cls} ({power_score}/100) ⚠️ هابط - " + reason
        
        if eval_result["warnings"]:
            reason += " | " + " ".join(eval_result["warnings"])
        
        # دمج مع full data
        merged = {
            **full_data,
            "action": action,
            "confidence": confidence,
            "reason": reason,
            "stop": full_data.get("stop", 0),
            "target": full_data.get("target1", 0),
            "target2": full_data.get("target2", 0),
            "holding_days": 3,
            "risk_level": _infer_risk_level(full_data),
            "rules_check": _format_rules_check(full_data),
            "composite_score": composite,
            "warnings": eval_result["warnings"],
        }
        picks.append(merged)
    
    # رفض ملخص
    rejected_summary = []
    for r in rejected_evals[:20]:  # أعلى 20 رفض فقط للتقرير
        rejected_summary.append({
            "ticker": r["ticker"],
            "reasons": r["rejections"],
        })
    
    # إحصاءات
    stats = {
        "total_candidates": len(candidates),
        "passed": len(passed_evals),
        "rejected": len(rejected_evals),
        "picks_selected": len(picks),
        "avg_composite_score": (
            round(sum(e["composite_score"] for e in passed_evals) / len(passed_evals), 3)
            if passed_evals else 0
        ),
        "avg_picks_ev": (
            round(sum(p.get("expected_value_pct", 0) for p in picks) / len(picks), 2)
            if picks else 0
        ),
    }
    
    result = {
        "date": today_str,
        "model": "rules_filter_v1",
        "no_ai": False,
        "filter_mode": "deterministic",
        "market_outlook": "محايد",
        "market_comment": (
            f"وضع No-API. تم تطبيق {len(RULES)} قواعد إقصاء صارمة. "
            f"نجح {stats['passed']}/{stats['total_candidates']} مرشح."
        ),
        "sector_analysis": "تحليل قطاعي يتطلب AI - معطّل في وضع rules_filter",
        "global_impact": "غير متاح في وضع rules_filter",
        "catch_up_opportunities": "غير متاح في وضع rules_filter",
        "picks": picks,
        "rejected_candidates": rejected_summary,
        "stats": stats,
        "weight_suggestions": {},  # لا نقترح تعديلات في هذا الوضع
        "learning_notes": (
            f"وضع rules_filter: نجح {stats['passed']} وفشل {stats['rejected']}. "
            f"متوسط composite={stats['avg_composite_score']}, "
            f"متوسط EV picks={stats['avg_picks_ev']}%."
        ),
        "missed_analysis": "تحليل الفرص الضائعة يتم في missed_opportunities.py",
        "risks_to_watch": _summarize_warnings(picks),
    }
    
    return result


def _empty_result(today_str: str) -> dict:
    return {
        "date": today_str,
        "model": "rules_filter_v1",
        "no_ai": False,
        "filter_mode": "deterministic",
        "market_outlook": "محايد",
        "market_comment": "لا توجد candidates للفلترة",
        "picks": [],
        "rejected_candidates": [],
        "stats": {"total_candidates": 0, "passed": 0, "rejected": 0, "picks_selected": 0},
        "weight_suggestions": {},
        "learning_notes": "",
        "missed_analysis": "",
        "risks_to_watch": "",
    }


def _infer_risk_level(candidate: dict) -> str:
    """يستنتج مستوى المخاطرة من البيانات."""
    adx = _safe(candidate.get("adx"))
    rr = _safe(candidate.get("risk_reward"))
    
    if adx > 30 and rr > 1.7:
        return "منخفض"
    elif adx > 20 and rr > 1.4:
        return "متوسط"
    else:
        return "مرتفع"


def _format_rules_check(candidate: dict) -> str:
    """ينسّق نتيجة فحص القواعد للعرض."""
    ev = _safe(candidate.get("expected_value_pct"))
    rr = _safe(candidate.get("risk_reward"))
    score = _safe(candidate.get("score"))
    weekly = candidate.get("weekly_trend", "?")
    return f"EV={ev:+.2f}%✓ | RR=1:{rr}✓ | weekly={weekly}✓ | score={score}✓"


def _summarize_warnings(picks: list) -> str:
    """يلخّص التحذيرات في picks."""
    all_warnings = []
    for p in picks:
        for w in p.get("warnings", []):
            all_warnings.append(f"{p['ticker']}: {w}")
    
    if not all_warnings:
        return "لا توجد تحذيرات حرجة في picks"
    return " | ".join(all_warnings[:5])


def log_filter_result(result: dict):
    """يحفظ نتيجة الـ filter في سجل (للتحليل لاحقاً)."""
    log_entry = {
        "date": result["date"],
        "stats": result.get("stats", {}),
        "picks_tickers": [p.get("ticker") for p in result.get("picks", [])],
        "picks_composite_avg": result.get("stats", {}).get("avg_composite_score", 0),
    }
    
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        with open(F_FILTER_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"لم يمكن حفظ filter log: {e}")


# ════════════════════════════════════════════════
# دالة التشغيل الرئيسية (تستبدل ai_analyst_v9.run في وضع No-API)
# ════════════════════════════════════════════════
def run():
    """
    تُستدعى من run_all.py عند عدم توفر API key.
    
    تقرأ tasi_candidates.json، تُطبّق القواعد، تكتب ai_result.json.
    """
    data = load_json_safe(F_CANDIDATES)
    if not data:
        print("  ⚠️ لا توجد بيانات candidates")
        save_result(_empty_result(datetime.now().strftime("%Y-%m-%d")))
        return
    
    candidates = data.get("candidates", [])
    if not candidates:
        print("  ⚠️ candidates فارغة")
        save_result(_empty_result(datetime.now().strftime("%Y-%m-%d")))
        return
    
    print(f"  🔧 وضع Rules Filter (deterministic) — {len(candidates)} candidates")
    
    result = filter_candidates(candidates, top_n=7)
    save_result(result)
    log_filter_result(result)
    
    stats = result["stats"]
    print(f"  ✓ نجح: {stats['passed']}/{stats['total_candidates']} | "
          f"picks: {stats['picks_selected']} | "
          f"متوسط EV: +{stats['avg_picks_ev']}%")
    
    # تفاصيل picks
    if result["picks"]:
        print(f"  📋 أعلى 3 picks:")
        for p in result["picks"][:3]:
            print(f"     {p['ticker']} ({p.get('sector','?')}): "
                  f"composite={p.get('composite_score', 0):.2f} | "
                  f"EV={p.get('expected_value_pct', 0):+.2f}% | "
                  f"action={p.get('action')}")


def load_json_safe(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_result(result: dict):
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        with open(F_AI_RESULT, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"فشل حفظ ai_result: {e}")


# ════════════════════════════════════════════════
# CLI للاختبار
# ════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("Rules-Based Filter — V9.2")
    print(f"{'='*60}\n")
    
    print("📋 القواعد المُطبّقة:")
    for rule, value in RULES.items():
        print(f"   - {rule}: {value}")
    
    print("\n📊 أوزان Composite Score:")
    for component, weight in SCORE_WEIGHTS.items():
        print(f"   - {component}: {weight}")
    
    print("\n🚀 تشغيل على البيانات الحالية...\n")
    run()
