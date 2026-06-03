# -*- coding: utf-8 -*-
"""
Rules-Based Filter — V9.2.4 (Tightened)
=========================================
الهدف:
    استبدال Claude/Opus بـ filter حتمي مبني على قواعد صارمة.

تغييرات V9.2.4 (بناءً على تحليل أسبوع 7-15 مايو 2026):
    ============================================================
    📊 البيانات قالت:
    - Score ≥ 20: WR=83%, avg=+2.78%  ← هذا فقط ما نريد
    - Score < 20: WR=21%, avg=-2.10%  ← فلتر منخفض جداً سابقاً
    - default signal type: WR=0%, n=3
    - mean_reversion: WR=0%, n=1
    - بتروكيماويات (في أيام تدفق -1.5B): WR=0%, n=5
    ============================================================
    
    ✅ P0: رفع min_score من 4.0 إلى 18.0 (الفرق الحاسم)
    ✅ P0: فلتر sector flow < -500M SAR (يستبعد قطاعات ناقصة سيولة)
    ✅ P0: استبعاد signal_type = default و mean_reversion (WR=0%)
    ✅ P0: فلتر RSI ≥ 72 (overbought - دخول متأخر)
    ✅ P1: رفع min_active_signals من 3 إلى 5 (إجماع أقوى)
    ✅ P1: رفع min_adx من 15 إلى 20 (إلا في breakout+volume)
    ✅ P1: تخفيض الـ picks من 7 إلى 5 (انتقائية أعلى)
    ✅ تسجيل sector_flow + ATR في الـ picks للـ paper_trading_engine
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
F_SECTOR_FLOWS = BASE / "sector_flows_prev.json"


# ════════════════════════════════════════════════
# 🎯 V9.2.4: قواعد الإقصاء المُشدّدة
# ════════════════════════════════════════════════
RULES = {
    # EV >= 0.0%: لا نشترط EV موجب صارم (EV حسابي تقريبي وليس مقياس edge)
    "min_ev_pct": 0.0,

    # RR >= 1.3: حد أدنى لنسبة المكافأة/المخاطرة
    "min_risk_reward": 1.3,

    # ════════════════════════════════════════════════════════════
    # 🔴 V9.2.4 التغيير الأهم: إلغاء عتبة score المطلقة (كانت 18.0)
    # ════════════════════════════════════════════════════════════
    # السبب الجذري للعطل: مقياس score متغيّر (الأوزان تتكيّف يومياً عبر
    # حلقة التعلّم)، فأقصى score في مرشحي اليوم = 13.56 بينما العتبة 18.0.
    # → رفض 100% من المرشحين كل يوم لأسابيع. العتبة "فوق سقف المقياس".
    # إحصائية "score≥20 → WR=83%" كانت على n=6 فقط ومن مقياس قديم (overfit).
    # التحقق الخالي من survivorship (1560 صف): score_raw لا يتنبأ بالعائد
    # المستقبلي في النطاق المتاح [6-16]. لذا:
    #   - لا عتبة score مطلقة
    #   - بوابة نسبية: أعلى score_percentile من مرشحي اليوم نفسه (scale-robust)
    "use_relative_score_gate": True,
    "score_percentile": 0.60,          # نقبل فقط أعلى 40% من مرشحي اليوم
    "absolute_score_floor": 0.0,        # أمان فقط؛ لا عتبة صارمة بعد الآن

    # ════════════════════════════════════════════════════════════
    # 🔴 V9.2.4 الإصلاح الاستراتيجي الأهم: منع مطاردة القفزات (anti-chasing)
    # ════════════════════════════════════════════════════════════
    # التحقق الخالي من survivorship أثبت أن دخول الأسهم التي قفزت كثيراً
    # في نفس اليوم يعطي أسوأ عائد مستقبلي:
    #   change[3,5)%  → avgFwd3 = -1.26%
    #   change[5,8)%  → avgFwd3 = -2.25%
    #   change[8+]%   → avgFwd3 = -3.87%
    # وهذا يفسّر paper trades: 13/39 صفقة MFE<0.5% (اشترينا القمة بالضبط).
    "block_chasing": True,
    "max_same_day_change": 6.0,        # رفض صارم إذا قفز السهم ≥ 6% اليوم
    "warn_same_day_change": 4.0,       # تحذير 4-6%

    # ADX >= 20: تجنّب الأسواق العرضية (مدعوم: adx≥30 أفضل من adx<20 بـ ~1pp)
    "min_adx": 20.0,

    # 🆕 V9.2.4: قوة نسبية vs TASI (مدعوم: rs≥1.0 أفضل باستمرار)
    "min_rs_vs_tasi": 1.0,

    # weekly_trend ≠ "هابط"
    "block_weekly_haboot": True,
    "weekly_haboot_min_signals": 6,

    # MTF: على الأقل 1 من المتاح (نُفضّل 2+)
    "min_mtf_aligned": 1,
    "preferred_mtf_aligned": 2,

    # عدد الإشارات النشطة
    "min_active_signals": 5,

    # فلتر القطاعات السلبية (نُبقيه لكن بعتبة override نسبية لاحقاً)
    "block_negative_sectors": True,
    "negative_sector_flow_threshold": -500_000_000,

    # ════════════════════════════════════════════════════════════
    # 🔴 V9.2.4: signal_type لم يعد بوابة رفض
    # ════════════════════════════════════════════════════════════
    # السبب: _infer_signal_type القديم كان يصنّف أي مرشح فيه إشارة RSI/MFI
    # كـ "mean_reversion" ويرفضه (29/47 يوم 22 مايو) حتى لو كان momentum قوي.
    # نحتفظ بـ signal_type كـ metadata فقط (للـ paper_trading_engine).
    "blocked_signal_types": [],

    # 🟡 V9.2.4: RSI/MFI أصبحا تحذيراً لا رفضاً
    # التحقق: rsi[70+] → avgFwd3 -0.52% (ليس أسوأ من المتوسط). لا مبرر لرفض صارم.
    "warn_rsi": 75.0,
    "warn_mfi": 88.0,

    # حد أدنى للسيولة
    "min_volume_ratio": 0.8,

    # ════════════════════════════════════════════════════════════
    # 🟡 V9.2.4 (اختياري): تقييد التمدّد فوق SMA20 — استراتيجية C
    # ════════════════════════════════════════════════════════════
    # backtest خالٍ من survivorship (9 أيام): تقييد ≤8% رفع متوسط عائد 3 أيام
    # من +0.05% إلى +0.14% والإصابة 20%→22%. أثر صغير وعيّنة صغيرة، لكنه متّسق
    # مع مبدأ anti-chasing (كلاهما: لا تدخل سهماً متمدّداً). الحد سخيّ (8%) فنادراً
    # ما يقيّد. للإطفاء: enable_extension_cap = False.
    "enable_extension_cap": True,
    "max_dist_from_sma20": 8.0,
}


# ════════════════════════════════════════════════
# عوامل الـ Composite Score
# ════════════════════════════════════════════════
SCORE_WEIGHTS = {
    "score": 0.25,
    "ev_pct": 0.20,           # خفّضنا من 0.25
    "risk_reward": 0.10,      # خفّضنا من 0.13
    "adx": 0.08,
    "mtf_alignment": 0.10,
    "volume_signal": 0.05,
    "power_score": 0.15,
    "sector_flow": 0.07,      # 🆕 V9.2.4: تدفق سيولة القطاع
}
# المجموع: 1.00 ✓


# ════════════════════════════════════════════════
# تحميل sector flows (يُستخدم في الفلترة)
# ════════════════════════════════════════════════
def _load_sector_flows():
    """يحمّل تدفقات القطاعات من sector_flows_prev.json.
    
    البنية الفعلية: {"sector_name": {"net_flow_5d": value_in_millions, ...}}
    نُرجع dict مُسطّحة: {"sector_name": flow_in_sar}
    """
    if not F_SECTOR_FLOWS.exists():
        return {}
    try:
        with open(F_SECTOR_FLOWS, "r", encoding="utf-8") as f:
            data = json.load(f)
        flat = {}
        for sector, info in data.items():
            if isinstance(info, dict):
                # net_flow_5d بالمليون → نحوّل لـ SAR
                flow_m = info.get("net_flow_5d") or info.get("net_flow") or 0
                flat[sector] = float(flow_m) * 1_000_000
            else:
                # إذا كانت رقماً مباشراً (legacy)
                try:
                    flat[sector] = float(info)
                except (ValueError, TypeError):
                    flat[sector] = 0.0
        return flat
    except Exception as e:
        log.warning(f"فشل تحميل sector_flows: {e}")
        return {}


def _get_sector_flow_for_candidate(candidate, sector_flows):
    """يحصل على تدفق القطاع لهذا candidate (بالريال السعودي)."""
    if candidate.get("sector_flow") is not None:
        try:
            return float(candidate["sector_flow"])
        except (ValueError, TypeError):
            pass
    
    sector = candidate.get("sector") or candidate.get("sector_name")
    if sector and sector in sector_flows:
        try:
            return float(sector_flows[sector])
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _safe(v, default=0.0):
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════
# تقييم candidate
# ════════════════════════════════════════════════
def evaluate_candidate(candidate: dict, sector_flows: dict = None,
                       score_floor: float = 0.0) -> dict:
    """
    يقيّم candidate واحد ضد القواعد، يُرجع تقريراً مفصّلاً.
    score_floor: عتبة score نسبية محسوبة من توزيع مرشحي اليوم (V9.2.4).
    """
    if sector_flows is None:
        sector_flows = {}
    
    ticker = candidate.get("ticker", "?")
    rejections = []
    warnings = []
    
    # ─── استخراج البيانات ───
    score = _safe(candidate.get("score"))
    ev_pct = _safe(candidate.get("expected_value_pct"))
    rr = _safe(candidate.get("risk_reward"))
    adx = _safe(candidate.get("adx"))
    rsi = _safe(candidate.get("rsi"))
    mfi = _safe(candidate.get("mfi"))
    change_pct = _safe(candidate.get("change"))          # 🆕 V9.2.4 anti-chasing
    rs_vs_tasi = _safe(candidate.get("rs_vs_tasi"), 1.0)  # 🆕 V9.2.4
    weekly = candidate.get("weekly_trend", "")
    signals = candidate.get("signals", []) or []
    mtf_aligned = _safe(candidate.get("mtf_aligned"))
    mtf_available = _safe(candidate.get("mtf_available"))
    volume_ratio = _safe(candidate.get("volume_ratio"))
    sector_flow = _get_sector_flow_for_candidate(candidate, sector_flows)
    
    # حقن sector_flow في candidate حتى scoring يستخدمه
    candidate["sector_flow"] = sector_flow
    
    # signal_type = metadata فقط (V9.2.4: لم يعد بوابة رفض)
    signal_type = _infer_signal_type(signals)
    candidate["signal_type"] = signal_type
    if signal_type in RULES["blocked_signal_types"]:
        rejections.append(f"signal_type={signal_type} ممنوع")
    
    # ─── القواعد الصارمة ───
    
    # القاعدة 1: EV
    if ev_pct < RULES["min_ev_pct"]:
        rejections.append(f"EV={ev_pct:+.2f}% (< {RULES['min_ev_pct']})")
    
    # القاعدة 2: RR
    if rr < RULES["min_risk_reward"]:
        rejections.append(f"RR=1:{rr} (< 1:{RULES['min_risk_reward']})")
    
    # 🔴 القاعدة 3 (V9.2.4): بوابة score نسبية بدل المطلقة
    # نرفض فقط إذا كان score أقل من عتبة اليوم (أدنى 60% من المرشحين)
    if RULES["use_relative_score_gate"]:
        if score < score_floor:
            rejections.append(
                f"score={score:.1f} ضمن أدنى مرشحي اليوم (< عتبة {score_floor:.1f})"
            )
    elif score < RULES.get("absolute_score_floor", 0):
        rejections.append(f"score={score:.1f} (< {RULES['absolute_score_floor']})")

    # 🔴 القاعدة 3ب (V9.2.4): anti-chasing — أهم إصلاح استراتيجي
    if RULES["block_chasing"]:
        if change_pct >= RULES["max_same_day_change"]:
            rejections.append(
                f"مطاردة قفزة: +{change_pct:.1f}% اليوم (≥ {RULES['max_same_day_change']}%؛ "
                f"تاريخياً عائد 3 أيام أسوأ)"
            )
        elif change_pct >= RULES["warn_same_day_change"]:
            warnings.append(f"⚠️ قفز +{change_pct:.1f}% اليوم - دخول متأخر محتمل")

    # 🆕 القاعدة 3ج (V9.2.4): قوة نسبية vs TASI
    if rs_vs_tasi < RULES["min_rs_vs_tasi"]:
        rejections.append(
            f"RS={rs_vs_tasi:.2f} < {RULES['min_rs_vs_tasi']} (أضعف من المؤشر)"
        )

    # 🟡 القاعدة 3د (V9.2.4 اختياري): تقييد التمدّد فوق SMA20 (استراتيجية C)
    if RULES.get("enable_extension_cap"):
        fsnap = candidate.get("feature_snapshot", {}) or {}
        dist20 = fsnap.get("dist_from_sma20_pct")
        if dist20 is not None and dist20 > RULES["max_dist_from_sma20"]:
            rejections.append(
                f"متمدّد +{dist20:.1f}% فوق SMA20 (> {RULES['max_dist_from_sma20']}%)"
            )
    
    # القاعدة 4: ADX
    has_volume_breakout = (
        "volume_surge" in signals and
        "breakout" in signals and
        volume_ratio >= 2.0
    )
    if adx < RULES["min_adx"] and not has_volume_breakout:
        rejections.append(f"ADX={adx:.1f} (< {RULES['min_adx']})")
    elif adx < RULES["min_adx"] and has_volume_breakout:
        warnings.append(
            f"ADX={adx:.1f} منخفض لكن volume {volume_ratio:.1f}× + breakout - early breakout مقبول"
        )
    
    # القاعدة 5: weekly trend
    if RULES["block_weekly_haboot"] and weekly == "هابط":
        if len(signals) < RULES["weekly_haboot_min_signals"]:
            rejections.append(
                f"weekly=هابط بدون إجماع كافٍ ({len(signals)} < {RULES['weekly_haboot_min_signals']})"
            )
        else:
            warnings.append(
                f"weekly=هابط لكن إجماع {len(signals)} إشارات قوي - مقبول بحذر"
            )
    
    # القاعدة 6: MTF
    if mtf_available > 0 and mtf_aligned < RULES["min_mtf_aligned"]:
        rejections.append(f"MTF aligned={mtf_aligned}/{mtf_available} ضعيف")
    elif mtf_available > 0 and mtf_aligned < RULES["preferred_mtf_aligned"]:
        warnings.append(f"MTF={mtf_aligned}/{mtf_available} مقبول لكن غير مثالي")
    
    # القاعدة 7: Active signals
    if len(signals) < RULES["min_active_signals"]:
        rejections.append(
            f"إشارات={len(signals)} (< {RULES['min_active_signals']})"
        )
    
    # 🟡 القاعدة 8 (V9.2.4): RSI تحذير لا رفض (التحقق لم يدعم الرفض)
    if rsi > 0 and rsi >= RULES["warn_rsi"]:
        warnings.append(f"⚠️ RSI={rsi:.0f} مرتفع (overbought)")
    
    # 🟡 القاعدة 9 (V9.2.4): MFI تحذير لا رفض
    if mfi > 0 and mfi >= RULES["warn_mfi"]:
        warnings.append(f"⚠️ MFI={mfi:.0f} مرتفع")
    
    # 🆕 القاعدة 10: Volume ratio
    if volume_ratio > 0 and volume_ratio < RULES["min_volume_ratio"]:
        rejections.append(
            f"vol_ratio={volume_ratio:.2f}× < {RULES['min_volume_ratio']} (سيولة ضعيفة)"
        )
    
    # 🆕 القاعدة 11: P0 - Sector flow (V9.2.4: تحذير لا رفض - العيّنة صغيرة)
    if RULES["block_negative_sectors"]:
        threshold = RULES["negative_sector_flow_threshold"]
        if sector_flow < threshold:
            warnings.append(
                f"⚠️ تدفق القطاع سلبي ({sector_flow/1e6:.0f}M ريال)"
            )
    
    # ─── تحذيرات إضافية ───
    
    # تحذير: breakout بدون volume
    if "breakout" in signals and volume_ratio < 1.0:
        warnings.append(
            f"⚠️ breakout بدون حجم (vol={volume_ratio:.1f}×) - خطر false breakout"
        )
    
    # تحذير: RSI 65-72 = warning zone
    if 65 <= rsi < 72:
        warnings.append(f"⚠️ RSI={rsi:.0f} يقترب من overbought")
    
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


def _infer_signal_type(signals: list) -> str:
    """يستنتج signal_type من قائمة signals.
    V9.2.4: الأولوية للزخم/الاتجاه. لا نصنّف كـ mean_reversion إلا إذا كانت
    الإشارات أوسيليتر بحتة (بلا اختراق/زخم/اتجاه).
    """
    if not signals:
        return "default"
    sig_set = set(signals)
    trend_momo = {"breakout", "volume_surge", "macd", "obv", "adx",
                  "supertrend", "ichimoku", "sma_cross", "relative_strength",
                  "weekly_trend"}
    if "breakout" in sig_set and "volume_surge" in sig_set:
        return "breakout"
    if sig_set & trend_momo:
        return "momentum"
    if "fibonacci" in sig_set or "vwap" in sig_set:
        return "support_bounce"
    if sig_set & {"rsi", "stoch_rsi", "mfi", "bollinger", "cmf"}:
        return "mean_reversion"
    return "default"


def compute_composite_score(candidate: dict) -> tuple:
    """
    يحسب composite score لترتيب الـ candidates الناجحين.
    8 مكوّنات الآن (+sector_flow).
    """
    components = {}
    
    # 1. Score (V9.2.4: مُطبَّع على المقياس الحقيقي الحالي ~ [0..15])
    # المقياس القديم (score-18)/(30-18) كان يُنتج 0 لكل score مُتاح < 18.
    score = _safe(candidate.get("score"))
    components["score"] = min(max(score / 15.0, 0.0), 1.0)
    
    # 2. EV
    ev_pct = _safe(candidate.get("expected_value_pct"))
    components["ev_pct"] = min(max(ev_pct / 5.0, 0.0), 1.0)
    
    # 3. RR
    rr = _safe(candidate.get("risk_reward"))
    components["risk_reward"] = min(max((rr - 1.3) / (2.5 - 1.3), 0.0), 1.0)
    
    # 4. ADX
    adx = _safe(candidate.get("adx"))
    components["adx"] = min(max((adx - 20) / (40 - 20), 0.0), 1.0)
    
    # 5. MTF alignment
    mtf_aligned = _safe(candidate.get("mtf_aligned"))
    mtf_available = _safe(candidate.get("mtf_available"), 1)
    components["mtf_alignment"] = mtf_aligned / max(mtf_available, 1)
    
    # 6. Volume signal
    volume_ratio = _safe(candidate.get("volume_ratio"))
    components["volume_signal"] = min(max((volume_ratio - 0.5) / (2.0 - 0.5), 0.0), 1.0)
    
    # 7. Power Score
    power_score = _safe(candidate.get("power_score"))
    if power_score >= 50:
        components["power_score"] = (power_score - 50) / 50.0
    else:
        components["power_score"] = 0.0
    
    # 8. 🆕 Sector flow (مُطبَّع: +500M → 1.0، -500M → 0.0)
    sector_flow = _safe(candidate.get("sector_flow"))
    # نُطبّع على نطاق ±1B SAR
    normalized = (sector_flow + 1_000_000_000) / 2_000_000_000
    components["sector_flow"] = min(max(normalized, 0.0), 1.0)
    
    composite = sum(components[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
    
    return round(composite, 4), components


def filter_candidates(candidates: list, top_n: int = 5) -> dict:
    """
    🎯 الدالة الرئيسية - V9.2.4: top_n خُفّض من 7 إلى 5 (انتقائية أعلى).
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    if not candidates:
        return _empty_result(today_str)
    
    # تحميل sector flows
    sector_flows = _load_sector_flows()
    log.info(f"loaded {len(sector_flows)} sector flows for filtering")

    # 🔴 V9.2.4: عتبة score نسبية من توزيع مرشحي اليوم (scale-robust)
    score_floor = 0.0
    if RULES.get("use_relative_score_gate"):
        all_scores = sorted(_safe(c.get("score")) for c in candidates)
        if all_scores:
            idx = int(len(all_scores) * RULES["score_percentile"])
            idx = min(idx, len(all_scores) - 1)
            score_floor = all_scores[idx]
        log.info(f"relative score floor (p{int(RULES['score_percentile']*100)}) = {score_floor:.2f}")

    # تقييم كل candidate
    evaluations = [evaluate_candidate(c, sector_flows, score_floor) for c in candidates]
    
    # فصل passed و rejected
    passed_evals = [e for e in evaluations if e["passed"]]
    rejected_evals = [e for e in evaluations if not e["passed"]]
    
    # ترتيب الناجحين بـ composite score
    passed_evals.sort(key=lambda x: -x["composite_score"])
    
    # اختيار top-N
    picks_evals = passed_evals[:top_n]
    
    # بناء picks
    candidates_by_ticker = {c["ticker"]: c for c in candidates}
    picks = []
    for eval_result in picks_evals:
        t = eval_result["ticker"]
        full_data = candidates_by_ticker.get(t, {})
        
        composite = eval_result["composite_score"]
        if composite >= 0.70:
            action = "شراء قوي"
        elif composite >= 0.50:
            action = "شراء"
        else:
            action = "مراقبة"
        
        confidence = int(composite * 100)
        
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
            reason += " | " + " ".join(eval_result["warnings"][:2])
        
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
            # 🆕 V9.2.4: ضمان وجود sector_flow و atr في picks
            "sector_flow": full_data.get("sector_flow", 0),
            "atr": full_data.get("atr") or full_data.get("atr_14"),
            "signal_type": full_data.get("signal_type", "default"),
        }
        picks.append(merged)
    
    # رفض ملخص
    rejected_summary = []
    for r in rejected_evals[:30]:
        rejected_summary.append({
            "ticker": r["ticker"],
            "reasons": r["rejections"],
        })
    
    # إحصاءات الرفض حسب سبب
    rejection_reasons_count = {}
    for r in rejected_evals:
        for reason in r["rejections"]:
            # نأخذ أول كلمة كـ category
            cat = reason.split(" ")[0].split("=")[0]
            rejection_reasons_count[cat] = rejection_reasons_count.get(cat, 0) + 1
    
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
        "rejection_reasons": rejection_reasons_count,
    }
    
    result = {
        "date": today_str,
        "model": "rules_filter_v2_4",
        "version": "V9.2.4",
        "no_ai": False,
        "filter_mode": "deterministic",
        "market_outlook": "محايد",
        "market_comment": (
            f"وضع No-API V9.2.4. تم تطبيق {len(RULES)} قواعد إقصاء صارمة. "
            f"نجح {stats['passed']}/{stats['total_candidates']} مرشح. "
            f"عتبة score نسبية (أعلى {int((1-RULES['score_percentile'])*100)}% من مرشحي اليوم) + منع مطاردة القفزات."
        ),
        "sector_analysis": "تحليل قطاعي يتطلب AI - معطّل في وضع rules_filter",
        "global_impact": "غير متاح في وضع rules_filter",
        "catch_up_opportunities": "غير متاح في وضع rules_filter",
        "picks": picks,
        "rejected_candidates": rejected_summary,
        "stats": stats,
        "weight_suggestions": {},
        "learning_notes": (
            f"V9.2.4 الفلتر المُشدّد: نجح {stats['passed']} وفشل {stats['rejected']}. "
            f"متوسط composite={stats['avg_composite_score']}, "
            f"متوسط EV picks={stats['avg_picks_ev']}%. "
            f"أكثر أسباب الرفض: {sorted(rejection_reasons_count.items(), key=lambda x: -x[1])[:3]}"
        ),
        "missed_analysis": "تحليل الفرص الضائعة يتم في missed_opportunities.py",
        "risks_to_watch": _summarize_warnings(picks),
    }
    
    return result


def _empty_result(today_str: str) -> dict:
    return {
        "date": today_str,
        "model": "rules_filter_v2_4",
        "version": "V9.2.4",
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
    adx = _safe(candidate.get("adx"))
    rr = _safe(candidate.get("risk_reward"))
    score = _safe(candidate.get("score"))
    
    if adx > 30 and rr > 1.7 and score > 25:
        return "منخفض"
    elif adx > 22 and rr > 1.4 and score > 20:
        return "متوسط"
    else:
        return "مرتفع"


def _format_rules_check(candidate: dict) -> str:
    ev = _safe(candidate.get("expected_value_pct"))
    rr = _safe(candidate.get("risk_reward"))
    score = _safe(candidate.get("score"))
    weekly = candidate.get("weekly_trend", "?")
    return f"EV={ev:+.2f}%✓ | RR=1:{rr}✓ | weekly={weekly}✓ | score={score:.1f}✓"


def _summarize_warnings(picks: list) -> str:
    all_warnings = []
    for p in picks:
        for w in p.get("warnings", []):
            all_warnings.append(f"{p['ticker']}: {w}")
    
    if not all_warnings:
        return "لا توجد تحذيرات حرجة في picks"
    return " | ".join(all_warnings[:5])


def log_filter_result(result: dict):
    log_entry = {
        "date": result["date"],
        "version": result.get("version", "V9.2.4"),
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
def run():
    """يُستدعى من run_all.py عند عدم توفر API key."""
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
    
    print(f"  🔧 Rules Filter V9.2.4 (مُشدّد) — {len(candidates)} candidates")
    
    result = filter_candidates(candidates, top_n=5)
    save_result(result)
    log_filter_result(result)
    
    stats = result["stats"]
    print(f"  ✓ نجح: {stats['passed']}/{stats['total_candidates']} | "
          f"picks: {stats['picks_selected']} | "
          f"متوسط EV: +{stats['avg_picks_ev']}%")
    
    if result["picks"]:
        print(f"  📋 Picks المختارة:")
        for p in result["picks"]:
            print(f"     {p['ticker']} ({p.get('sector','?')}): "
                  f"composite={p.get('composite_score', 0):.2f} | "
                  f"score={p.get('score', 0):.1f} | "
                  f"EV={p.get('expected_value_pct', 0):+.2f}% | "
                  f"action={p.get('action')}")
    
    # طباعة أكثر أسباب الرفض
    rej_reasons = stats.get("rejection_reasons", {})
    if rej_reasons:
        top_reasons = sorted(rej_reasons.items(), key=lambda x: -x[1])[:5]
        print(f"  🚫 أكثر أسباب الرفض: {top_reasons}")


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


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("Rules-Based Filter — V9.2.4 (Tightened)")
    print(f"{'='*60}\n")
    
    print("📋 القواعد المُطبّقة:")
    for rule, value in RULES.items():
        print(f"   - {rule}: {value}")
    
    print("\n📊 أوزان Composite Score:")
    for component, weight in SCORE_WEIGHTS.items():
        print(f"   - {component}: {weight}")
    
    print("\n🚀 تشغيل على البيانات الحالية...\n")
    run()
