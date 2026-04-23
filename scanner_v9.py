# -*- coding: utf-8 -*-
"""
ماسح تداول V9 — الإصدار الاحترافي
=============================================
الإصلاحات الحرجة من V8:
  ✅ fetch_macro يعمل (كان كل البيانات = N/A)
  ✅ evaluate_yesterday لا يقارن اليوم بنفسه (حل مشكلة 0/0)
  ✅ تعريف hit واقعي: +1.5% خلال 3 أيام بدون ضرب وقف 2%
  ✅ VWAP حقيقي بدلاً من Volume-Weighted MA المغلوط
  ✅ OBV vectorized (أسرع 50×)
  ✅ لا تكرار في SECTOR_MAP (كان 4050 في قطاعين)
  ✅ period=200d لضمان صحة SMA50 و SMA200

الجديد في V9:
  ⭐ 18 مؤشر (بدلاً من 11)
  ⭐ ADX + Supertrend + Ichimoku + MFI + Fibonacci + CMF + RS
  ⭐ Correlation matrix + Sector rotation + Liquidity flow
  ⭐ Expected Value scoring
  ⭐ XGBoost ML probability (إذا كانت البيانات كافية)
  ⭐ Confidence weighted with ADX (اتجاه قوي = ثقة أعلى)
"""
import json
import os
import time
import logging
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

from data_sources import fetch_ohlcv_batch, fetch_macro, fetch_ohlcv
from indicators import compute_all
from correlation_engine import build_intermarket_summary, SECTOR_LEADERS
from ml_engine import (
    extract_features_from_snapshot, append_training_data,
    realistic_hit_label, predict_probability, train_model,
    suggest_weights_from_importance,
)

# V9.1 modules — graceful fallback if not available
try:
    from mtf_engine import check_mtf_alignment
    MTF_AVAILABLE = True
except ImportError:
    MTF_AVAILABLE = False
    def check_mtf_alignment(ticker):
        return {"aligned_count": 0, "mtf_multiplier": 1.0, "details": "MTF غير متاح"}

try:
    from news_engine import get_sentiment_for_all, get_ticker_multiplier
    NEWS_AVAILABLE = True
except ImportError:
    NEWS_AVAILABLE = False

try:
    from earnings_calendar import get_earnings_calendar, get_earnings_multiplier
    EARNINGS_AVAILABLE = True
except ImportError:
    EARNINGS_AVAILABLE = False

BASE = Path("tadawul_data")
try:
    if not BASE.exists():
        BASE.mkdir(parents=True, exist_ok=True)
except FileExistsError:
    pass

F_WEIGHTS    = BASE / "tasi_weights.json"
F_HISTORY    = BASE / "tasi_history.json"
F_TRACKER    = BASE / "tasi_tracker.json"
F_CANDIDATES = BASE / "tasi_candidates.json"
F_SECTOR_PREV = BASE / "sector_flows_prev.json"
F_LOG        = BASE / "scanner.log"

try:
    logging.basicConfig(
        filename=str(F_LOG), level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s", encoding="utf-8",
    )
except Exception:
    # fallback to stderr if file not accessible
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════
# معلمات قابلة للضبط
# ════════════════════════════════════════════════
MIN_PRICE = 5.0           # خفضنا من 10 لعدم إغفال فرص
MIN_SCORE = 3.0           # رفعنا من 2.5 لجودة أعلى (مع المؤشرات الأكثر)
TOP_N = 50
DECAY = 0.995             # أبطأ من V8 (كان 0.98 سريع جداً)
W_MIN, W_MAX = 0.2, 3.0   # نطاق أضيق لمنع تطرف الأوزان
HIT_TARGET_PCT = 1.5      # تعريف hit واقعي
HIT_STOP_PCT = -2.0
HIT_DAYS = 3

# ════════════════════════════════════════════════
# قائمة الأسهم + الخريطة القطاعية (مُصلحة)
# ════════════════════════════════════════════════
SECTOR_MAP = {
    "بتروكيماويات": {
        "tickers": ["2010","2020","2040","2050","2060","2070","2090","2110","2150","2160",
                    "2170","2180","2210","2220","2223","2240","2250","2290","2300","2310",
                    "2320","2330","2340","2360","2370","2380",
                    "1301","1302","1303","1304","1320","1321","1322","1323","1324"],
        "oil_weight": 1.0,
    },
    "بنوك": {
        "tickers": ["1010","1020","1030","1050","1060","1080","1120","1140","1150","1180"],
        "oil_weight": 0.3,
    },
    "طاقة": {
        "tickers": ["2222","2030","2381","2382"],
        "oil_weight": 0.9,
    },
    "اتصالات": {
        "tickers": ["7010","7020","7030","7040"],
        "oil_weight": 0.05,
    },
    "تقنية": {
        "tickers": ["7200","7202","7203","7211"],
        "oil_weight": 0.05,
    },
    "تجزئة": {
        "tickers": ["4003","4006","4008","4011","4020","4061","4160","4162","4163","4164","4165",
                    "4190","4191","4193","4194","4200","4240"],
        "oil_weight": 0.1,
    },
    "أسمنت": {
        "tickers": ["3003","3005","3010","3020","3030","3040","3050","3060","3080","3092"],
        "oil_weight": 0.15,
    },
    "تأمين": {
        "tickers": ["8010","8012","8030","8070","8120","8180","8200","8210","8230","8240",
                    "8250","8280","8300","8313"],
        "oil_weight": 0.05,
    },
    "زراعة وأغذية": {
        "tickers": ["2100","2270","2280","2281","2282","2283","2284","2285","2287",
                    "6001","6002","6004","6010","6012","6013","6014","6017","6019","6020",
                    "6050","6060","6070"],
        "oil_weight": 0.1,
    },
    "عقار": {
        "tickers": ["4090","4100","4150","4220","4230","4250","4280","4290","4291","4292",
                    "4300","4310","4320","4321","4322","4323","4325","4327"],
        "oil_weight": 0.15,
    },
    "خدمات مالية": {
        "tickers": ["1111","1182","1183","1202","1210","1211","1212","1213","1214",
                    "4081","4083","4084"],
        "oil_weight": 0.2,
    },
    "نقل": {
        # ملاحظة: 4050 نُقل إلى تجزئة فقط (كان مكرراً في V8)
        "tickers": ["2190","4030","4031","4040","4260","4261","4262","4263","4264","4265"],
        "oil_weight": 0.3,
    },
    "إعلام": {
        "tickers": ["4070","4071","4072","4210"],
        "oil_weight": 0.05,
    },
    "فنادق": {
        "tickers": ["1810","1830","1833"],
        "oil_weight": 0.1,
    },
    "مرافق": {
        "tickers": ["2080","2081","2082","2083","2084","5110"],
        "oil_weight": 0.2,
    },
    "رعاية صحية": {
        "tickers": ["4002","4004","4005","4007","4009","4013","4014","4015","4016","4017","4018","4019"],
        "oil_weight": 0.05,
    },
    "صناعية": {
        "tickers": ["1835","2120","2130","2140","4141","4142","4143","4144","4145","4146","4170"],
        "oil_weight": 0.2,
    },
}


def _build_ticker_sector():
    m = {}
    for sec, info in SECTOR_MAP.items():
        for t in info["tickers"]:
            m[t] = sec
    return m


TICKER_SECTOR = _build_ticker_sector()


def get_sector(code):
    return TICKER_SECTOR.get(code.replace(".SR", ""), "أخرى")


def get_oil_weight(code):
    sec = get_sector(code)
    return SECTOR_MAP.get(sec, {}).get("oil_weight", 0.1)


# جمع كل الرموز بلا تكرار
TASI_TICKERS = sorted(set(code for info in SECTOR_MAP.values() for code in info["tickers"]))


# ════════════════════════════════════════════════
# الأوزان الافتراضية لـ 18 مؤشر
# ════════════════════════════════════════════════
DEFAULT_WEIGHTS = {
    "rsi": 1.0,
    "stoch_rsi": 0.8,
    "macd": 1.0,
    "bollinger": 0.8,
    "obv": 0.9,
    "vwap": 0.9,
    "volume_surge": 1.2,
    "sma_cross": 1.0,
    "breakout": 1.1,
    "candle_pattern": 0.7,
    "oil_correlation": 0.5,
    "adx": 1.1,              # جديد
    "supertrend": 1.2,       # جديد
    "ichimoku": 1.0,         # جديد
    "mfi": 0.9,              # جديد
    "cmf": 0.8,              # جديد
    "fibonacci": 0.8,        # جديد
    "relative_strength": 1.0, # جديد
    "weekly_trend": 1.3,     # جديد (كان ضمن multi-timeframe فقط)
}

SIGNAL_NAMES_AR = {
    "rsi": "RSI", "stoch_rsi": "StochRSI", "macd": "MACD", "bollinger": "بولنجر",
    "obv": "OBV", "vwap": "VWAP", "volume_surge": "حجم", "sma_cross": "تقاطع ذهبي",
    "breakout": "اختراق", "candle_pattern": "شموع", "oil_correlation": "نفط",
    "adx": "ADX", "supertrend": "Supertrend", "ichimoku": "إيشيموكو",
    "mfi": "MFI", "cmf": "CMF", "fibonacci": "فيبوناتشي",
    "relative_strength": "قوة نسبية", "weekly_trend": "اتجاه أسبوعي",
}


# ════════════════════════════════════════════════
# أدوات مساعدة
# ════════════════════════════════════════════════
def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clamp_weights(w):
    return {k: max(W_MIN, min(W_MAX, v)) for k, v in w.items()}


def apply_decay(w):
    return clamp_weights({k: v * DECAY for k, v in w.items()})


def init_tracker():
    return {
        "signal_accuracy": {s: {"triggered": 0, "hit": 0, "miss": 0, "rate": 0.0}
                            for s in DEFAULT_WEIGHTS},
        "blacklist": [],
        "blacklist_date": {},  # code -> date added
        "stock_record": {},
        "daily_log": [],
        "ml_predictions_record": [],
    }


def _safe(v, default=0.0):
    if v is None:
        return default
    try:
        f = float(v)
        if np.isnan(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════
# حساب النقاط لكل سهم
# ════════════════════════════════════════════════
def score_stock(last, prev, weights, oil_chg_pct, code, ml_prob=None):
    score = 0.0
    reasons = []
    active = []

    rsi_v = _safe(last.get("rsi"), 50)
    stoch_k = _safe(last.get("stoch_rsi_k"), 50)
    stoch_d = _safe(last.get("stoch_rsi_d"), 50)
    stoch_k_prev = _safe(prev.get("stoch_rsi_k"), 50)
    macd_h = _safe(last.get("macd_hist"))
    macd_h_prev = _safe(prev.get("macd_hist"))
    bb_pct = _safe(last.get("bb_pct"), 0.5)
    obv_v = _safe(last.get("obv"))
    obv_ma = _safe(last.get("obv_ma"))
    close = _safe(last.get("Close"))
    prev_close = _safe(prev.get("Close"), close)
    vwap = _safe(last.get("vwap"))
    vol = _safe(last.get("Volume"))
    vma = _safe(last.get("vol_ma20"), 1)
    sma20 = _safe(last.get("sma20"))
    sma50 = _safe(last.get("sma50"))
    sma20_prev = _safe(prev.get("sma20"))
    sma50_prev = _safe(prev.get("sma50"))
    adx_v = _safe(last.get("adx"), 20)
    di_p = _safe(last.get("di_plus"), 20)
    di_m = _safe(last.get("di_minus"), 20)
    st_dir = _safe(last.get("supertrend_dir"))
    st_dir_prev = _safe(prev.get("supertrend_dir"))
    tenkan = _safe(last.get("tenkan"))
    kijun = _safe(last.get("kijun"))
    senkou_a = _safe(last.get("senkou_a"))
    senkou_b = _safe(last.get("senkou_b"))
    mfi_v = _safe(last.get("mfi"), 50)
    cmf_v = _safe(last.get("cmf"))
    fib_pos = _safe(last.get("fib_pos"), 0.5)
    rs_tasi = _safe(last.get("rs_vs_tasi"), 1.0)
    weekly_t = _safe(last.get("weekly_trend"))

    # 1. RSI
    if rsi_v < 35:
        boost = 1 + (35 - rsi_v) / 35
        score += weights.get("rsi", 1.0) * boost
        reasons.append(f"RSI={rsi_v:.0f} تشبع بيع")
        active.append("rsi")
    elif rsi_v > 70:
        score -= weights.get("rsi", 1.0) * 0.5

    # 2. Stoch RSI — تقاطع صاعد في منطقة التشبع
    if stoch_k < 20 and stoch_k > stoch_d and stoch_k_prev <= stoch_d:
        score += weights.get("stoch_rsi", 0.8)
        reasons.append(f"StochRSI تقاطع صاعد ({stoch_k:.0f})")
        active.append("stoch_rsi")
    elif stoch_k > 80:
        score -= weights.get("stoch_rsi", 0.8) * 0.3

    # 3. MACD — تقاطع hist من سالب لموجب
    if macd_h > 0 and macd_h_prev <= 0:
        score += weights.get("macd", 1.0)
        reasons.append("MACD تقاطع صاعد")
        active.append("macd")
    elif macd_h < 0 and macd_h_prev >= 0:
        score -= weights.get("macd", 1.0) * 0.3

    # 4. Bollinger squeeze + bounce
    if bb_pct < 0.15:
        score += weights.get("bollinger", 0.8)
        reasons.append(f"بولنجر {bb_pct:.0%}")
        active.append("bollinger")
    elif bb_pct > 0.95:
        score -= weights.get("bollinger", 0.8) * 0.3

    # 5. OBV
    if obv_ma != 0 and obv_v > obv_ma * 1.05:
        score += weights.get("obv", 0.9)
        reasons.append("OBV صاعد")
        active.append("obv")

    # 6. VWAP — السعر تحت VWAP قليلاً (فرصة)
    if vwap > 0:
        vwap_diff = (close - vwap) / vwap * 100
        if -3 < vwap_diff < 0:
            score += weights.get("vwap", 0.9)
            reasons.append(f"VWAP ({vwap_diff:+.1f}%)")
            active.append("vwap")

    # 7. Volume surge
    if vma > 0:
        vol_r = vol / vma
        if vol_r > 2:
            boost = min(vol_r / 2, 2.5)
            score += weights.get("volume_surge", 1.2) * boost
            reasons.append(f"حجم {vol_r:.1f}×")
            active.append("volume_surge")

    # 8. SMA Cross (golden cross)
    if sma20 > 0 and sma50 > 0 and sma20_prev > 0 and sma50_prev > 0:
        if sma20 > sma50 and sma20_prev <= sma50_prev:
            score += weights.get("sma_cross", 1.0)
            reasons.append("تقاطع ذهبي")
            active.append("sma_cross")

    # 9. Breakout
    if prev_close > 0:
        chg = (close - prev_close) / prev_close * 100
        if chg > 2:
            score += weights.get("breakout", 1.1)
            reasons.append(f"اختراق +{chg:.1f}%")
            active.append("breakout")

    # 10. Candle patterns
    if last.get("engulfing_bull"):
        score += weights.get("candle_pattern", 0.7)
        reasons.append("ابتلاع صاعد")
        active.append("candle_pattern")
    elif last.get("hammer") and rsi_v < 40:
        score += weights.get("candle_pattern", 0.7) * 0.8
        reasons.append("مطرقة")
        active.append("candle_pattern")

    # 11. Oil correlation
    ow = get_oil_weight(code)
    if oil_chg_pct > 1 and ow > 0.3:
        boost = ow * (oil_chg_pct / 2)
        score += weights.get("oil_correlation", 0.5) * boost
        reasons.append(f"نفط +{oil_chg_pct:.1f}%")
        active.append("oil_correlation")
    elif oil_chg_pct < -2 and ow > 0.5:
        score -= weights.get("oil_correlation", 0.5) * ow * 0.5

    # 12. ADX — اتجاه قوي
    if adx_v > 25 and di_p > di_m:
        boost = min((adx_v - 25) / 25, 1.5)
        score += weights.get("adx", 1.1) * boost
        reasons.append(f"ADX قوي ({adx_v:.0f})")
        active.append("adx")
    elif adx_v < 15:
        score -= 0.3  # سوق عرضي، لا تتداول breakouts

    # 13. Supertrend — تبديل لصاعد
    if st_dir == 1 and st_dir_prev == -1:
        score += weights.get("supertrend", 1.2) * 1.3
        reasons.append("Supertrend صاعد (تحول)")
        active.append("supertrend")
    elif st_dir == 1:
        score += weights.get("supertrend", 1.2) * 0.5
        active.append("supertrend")

    # 14. Ichimoku — فوق السحابة
    if close > max(senkou_a, senkou_b) > 0 and tenkan > kijun:
        score += weights.get("ichimoku", 1.0)
        reasons.append("فوق سحابة إيشيموكو")
        active.append("ichimoku")
    elif close < min(senkou_a, senkou_b) and senkou_a > 0:
        score -= weights.get("ichimoku", 1.0) * 0.5

    # 15. MFI — تشبع بيع
    if mfi_v < 25:
        score += weights.get("mfi", 0.9) * (1 + (25 - mfi_v) / 25)
        reasons.append(f"MFI={mfi_v:.0f}")
        active.append("mfi")
    elif mfi_v > 80:
        score -= weights.get("mfi", 0.9) * 0.4

    # 16. CMF — تراكم مؤسسي
    if cmf_v > 0.10:
        score += weights.get("cmf", 0.8)
        reasons.append(f"تراكم مؤسسي CMF={cmf_v:.2f}")
        active.append("cmf")
    elif cmf_v < -0.15:
        score -= weights.get("cmf", 0.8) * 0.6

    # 17. Fibonacci — عند مستويات ارتداد
    if 0.35 < fib_pos < 0.45:  # قريب من 38.2%
        score += weights.get("fibonacci", 0.8) * 0.9
        reasons.append(f"فيبو 38.2%")
        active.append("fibonacci")
    elif 0.55 < fib_pos < 0.68:  # قريب من 61.8%
        score += weights.get("fibonacci", 0.8)
        reasons.append(f"فيبو 61.8%")
        active.append("fibonacci")

    # 18. Relative Strength vs TASI
    if rs_tasi > 1.03:
        score += weights.get("relative_strength", 1.0)
        reasons.append(f"قوة نسبية +{(rs_tasi-1)*100:.1f}%")
        active.append("relative_strength")
    elif rs_tasi < 0.97:
        score -= 0.3

    # 19. Weekly trend filter
    if weekly_t == 1 and len(active) >= 2:
        score *= 1.25
        reasons.append("أسبوعي صاعد")
        active.append("weekly_trend")
    elif weekly_t == -1 and len(active) >= 2:
        score *= 0.75
        reasons.append("⚠️ أسبوعي هابط")

    # Confluence bonus
    if len(active) >= 4:
        bonus = 0.6 * (len(active) - 3)
        score += bonus
        reasons.append(f"إجماع {len(active)} ({bonus:+.1f})")

    # ML probability boost
    if ml_prob is not None:
        if ml_prob > 0.65:
            score *= 1.3
            reasons.append(f"🤖 ML {ml_prob:.0%}")
        elif ml_prob < 0.35:
            score *= 0.7

    return round(score, 2), reasons, active


# ════════════════════════════════════════════════
# تقييم توقعات الأمس (منطق واقعي)
# ════════════════════════════════════════════════
def evaluate_yesterday(weights, tracker):
    """
    يصلح bug V8: لا يُقارن اليوم بنفسه.
    يأخذ آخر predictions حيث التاريخ < اليوم.
    يستخدم تعريف hit واقعي (+1.5% في 3 أيام بدون ضرب وقف -2%).
    """
    history = load_json(F_HISTORY, [])
    today_str = datetime.now().strftime("%Y-%m-%d")

    # إيجاد آخر إدخال تاريخه قبل اليوم
    yesterday_entry = None
    for entry in reversed(history):
        if entry.get("date") != today_str:
            yesterday_entry = entry
            break

    if not yesterday_entry:
        return [], weights, tracker, "لا توجد توقعات سابقة قابلة للتقييم"

    preds = yesterday_entry.get("predictions", [])
    if not preds:
        return [], weights, tracker, "توقعات أمس فارغة"

    pred_date = yesterday_entry["date"]
    results = []
    hits = 0
    misses = 0
    pending = 0
    ml_training_rows = []

    for p in preds:
        t = p["ticker"]
        entry_close = p.get("close", 0)
        if entry_close <= 0:
            continue

        try:
            # جلب بيانات من تاريخ التوقع حتى اليوم
            df = fetch_ohlcv(f"{t}.SR", period_days=10)
            if df.empty:
                continue

            # فلترة البيانات بعد تاريخ التوقع
            df.index = pd.to_datetime(df.index)
            try:
                pred_ts = pd.Timestamp(pred_date)
                future = df[df.index > pred_ts]
            except Exception:
                future = df.tail(HIT_DAYS)

            if future.empty:
                pending += 1
                continue

            # تعريف hit الواقعي
            hit_label = realistic_hit_label(
                entry_close, future,
                target_pct=HIT_TARGET_PCT,
                stop_pct=HIT_STOP_PCT,
                days=HIT_DAYS,
            )
            if hit_label is None:
                pending += 1
                continue

            hit = bool(hit_label)
            if hit:
                hits += 1
            else:
                misses += 1

            # max/min خلال النافذة
            max_high = float(future.head(HIT_DAYS)["High"].max())
            min_low = float(future.head(HIT_DAYS)["Low"].min())
            max_pct = (max_high - entry_close) / entry_close * 100
            min_pct = (min_low - entry_close) / entry_close * 100

            results.append({
                "ticker": t,
                "predicted_close": entry_close,
                "max_high": round(max_high, 2),
                "min_low": round(min_low, 2),
                "max_pct": round(max_pct, 2),
                "min_pct": round(min_pct, 2),
                "hit": hit,
                "signals": p.get("signals", []),
            })

            # تحديث signal_accuracy
            for sig in p.get("signals", []):
                if sig in tracker["signal_accuracy"]:
                    acc = tracker["signal_accuracy"][sig]
                    acc["triggered"] += 1
                    if hit:
                        acc["hit"] += 1
                    else:
                        acc["miss"] += 1
                    acc["rate"] = round(acc["hit"] / max(acc["triggered"], 1), 3)

            # ضبط أوزان بناءً على الأداء الحقيقي
            for sig in p.get("signals", []):
                if sig in weights:
                    weights[sig] *= 1.015 if hit else 0.985

            # سجل الأسهم (للـ blacklist الذكي)
            sr = tracker.setdefault("stock_record", {})
            rec = sr.setdefault(t, {"hits": 0, "total": 0, "last_update": today_str})
            rec["total"] += 1
            rec["last_update"] = today_str
            if hit:
                rec["hits"] += 1
            # blacklist ديناميكي: بعد 7 محاولات بمعدل < 25%
            if rec["total"] >= 7 and rec["hits"] / rec["total"] < 0.25:
                if t not in tracker["blacklist"]:
                    tracker["blacklist"].append(t)
                    tracker.setdefault("blacklist_date", {})[t] = today_str

            # بناء training row للـ ML (إذا كان features محفوظة)
            feat_snap = p.get("feature_snapshot")
            if feat_snap:
                ml_training_rows.append({
                    **feat_snap,
                    "hit": int(hit),
                    "ticker": t,
                    "pred_date": pred_date,
                })

        except Exception as e:
            log.debug(f"Eval {t}: {e}")

    # تنظيف blacklist القديم (> 60 يوم)
    now_dt = datetime.now()
    bl_dates = tracker.get("blacklist_date", {})
    for t in list(tracker.get("blacklist", [])):
        d_str = bl_dates.get(t)
        if d_str:
            try:
                d = datetime.strptime(d_str, "%Y-%m-%d")
                if (now_dt - d).days > 60:
                    tracker["blacklist"].remove(t)
                    bl_dates.pop(t, None)
            except Exception:
                pass

    weights = clamp_weights(weights)

    # حفظ ML training data
    if ml_training_rows:
        append_training_data(ml_training_rows)

    total = hits + misses
    acc = round(hits / total * 100, 1) if total > 0 else 0
    summary = f"{hits}/{total} ({acc}%) | معلق:{pending}"

    return results, weights, tracker, summary


# ════════════════════════════════════════════════
# المسح الرئيسي
# ════════════════════════════════════════════════
def scan_tasi(weights, blacklist):
    print("  📡 جلب بيانات الماكرو...")
    macro = fetch_macro(use_cache=False)
    if macro.get("fetch_errors"):
        print(f"  ⚠️ أخطاء ماكرو: {macro['fetch_errors']}")
    else:
        print(f"  ✓ النفط: {macro.get('oil')} | S&P: {macro.get('sp500')} | TASI: {macro.get('tasi_index')}")

    oil_chg = macro.get("oil_chg", 0)

    # جلب بيانات TASI للمقارنة النسبية
    print("  📡 جلب بيانات TASI...")
    tasi_close = None
    try:
        tasi_df = fetch_ohlcv("^TASI.SR", period_days=120)
        if tasi_df is not None and not tasi_df.empty and "Close" in tasi_df.columns:
            tasi_close = tasi_df["Close"]
            print(f"  ✓ TASI: {len(tasi_close)} شمعة")
        else:
            print("  ⚠️ TASI index غير متاح — المقارنة النسبية ستُعطل")
    except Exception as e:
        print(f"  ⚠️ فشل جلب TASI: {e}")

    tickers = [f"{t}.SR" for t in TASI_TICKERS if t not in blacklist]
    print(f"  🔍 مسح {len(tickers)} سهم (مستبعد: {len(blacklist)})...")

    # جلب كل البيانات دفعة واحدة
    start = time.time()
    stocks_data = fetch_ohlcv_batch(tickers, period_days=200)
    print(f"  ✓ جُلبت {len(stocks_data)} سهم في {time.time()-start:.1f}s")

    # V9.1: جلب news sentiment + earnings (مرة واحدة لكل السوق)
    sentiment_data = {"sentiments": {}}
    earnings_data = {"earnings": {}}
    known_codes = set(TASI_TICKERS)

    if NEWS_AVAILABLE:
        print("  📰 جلب News sentiment...")
        try:
            sentiment_data = get_sentiment_for_all(known_codes)
            sent_count = len(sentiment_data.get("sentiments", {}))
            print(f"  ✓ Sentiment لـ {sent_count} سهم")
        except Exception as e:
            print(f"  ⚠️ فشل news sentiment: {e}")

    if EARNINGS_AVAILABLE:
        print("  📅 جلب تقويم الأرباح...")
        try:
            earnings_data = get_earnings_calendar(list(known_codes))
            print(f"  ✓ تقويم الأرباح ({earnings_data.get('method')})")
        except Exception as e:
            print(f"  ⚠️ فشل earnings calendar: {e}")

    candidates = []
    gainers = []
    sector_summary = {}
    errors = 0

    # حساب المؤشرات لكل سهم
    for ticker, df in stocks_data.items():
        try:
            if df.empty or len(df) < 50:
                continue

            # تنظيف
            df = df.dropna(subset=["Close"])
            if len(df) < 50:
                continue

            last_close = _safe(df["Close"].iloc[-1])
            prev_close = _safe(df["Close"].iloc[-2])
            if last_close < MIN_PRICE:
                continue
            if prev_close == 0:
                continue

            chg = (last_close - prev_close) / prev_close * 100
            code = ticker.replace(".SR", "")
            sec = get_sector(code)

            # ملخص القطاع
            if sec not in sector_summary:
                sector_summary[sec] = {"count": 0, "total_change": 0, "gainers": 0}
            sector_summary[sec]["count"] += 1
            sector_summary[sec]["total_change"] += chg
            if chg > 0:
                sector_summary[sec]["gainers"] += 1

            gainers.append({"ticker": code, "close": round(last_close, 2),
                            "change": round(chg, 2), "sector": sec})

            # حساب المؤشرات
            df = compute_all(df, benchmark_close=tasi_close)
            last = df.iloc[-1].to_dict()
            prev = df.iloc[-2].to_dict()

            # استخراج features لـ ML
            features = extract_features_from_snapshot(last, last_close)

            # ML prediction
            ml_prob = predict_probability(features)

            # Score (قبل multipliers V9.1)
            score, reasons, signals = score_stock(last, prev, weights, oil_chg, code, ml_prob)
            base_score = score  # نحتفظ بالـ score الأصلي للشفافية

            # ═══ V9.1: Apply multipliers ═══
            mtf_info = {"mtf_multiplier": 1.0, "aligned_count": 0,
                        "available_count": 0, "details": "", "daily_score": None,
                        "h4_score": None, "h1_score": None}
            news_info = {"multiplier": 1.0, "label": "no_news",
                         "score": 0, "headlines": [], "reason": ""}
            earnings_info = {"multiplier": 1.0, "message": None}

            # MTF (لا نُجري MTF لكل الأسهم — فقط التي تحمل score عالي كفاية لتقنيع CPU cost)
            # فقط الأسهم بـ base_score > MIN_SCORE - 0.5 تستحق MTF check
            if MTF_AVAILABLE and base_score >= (MIN_SCORE - 0.5):
                try:
                    mtf_info = check_mtf_alignment(ticker)
                    score *= mtf_info["mtf_multiplier"]
                    if mtf_info["mtf_multiplier"] > 1.0:
                        reasons.append(f"MTF {mtf_info['aligned_count']}/{mtf_info['available_count']} صاعد")
                    elif mtf_info["mtf_multiplier"] < 1.0:
                        reasons.append(f"⚠️ MTF ضعيف")
                except Exception as e:
                    log.debug(f"MTF {ticker}: {e}")

            # News sentiment
            if NEWS_AVAILABLE:
                mult, label = get_ticker_multiplier(code, sentiment_data)
                sent_detail = sentiment_data.get("sentiments", {}).get(code, {})
                news_info = {
                    "multiplier": mult,
                    "label": label,
                    "score": sent_detail.get("score", 0),
                    "headlines": sent_detail.get("headlines", []),
                    "reason": sent_detail.get("reason", ""),
                }
                if mult != 1.0:
                    score *= mult
                    if mult < 1.0:
                        reasons.append(f"📰 خبر سلبي ({label})")
                    else:
                        reasons.append(f"📰 خبر إيجابي ({label})")

            # Earnings calendar
            if EARNINGS_AVAILABLE:
                mult, msg = get_earnings_multiplier(code, earnings_data)
                earnings_info = {"multiplier": mult, "message": msg}
                if mult != 1.0:
                    score *= mult
                    if msg:
                        reasons.append(msg)

            score = round(score, 2)
            # ═══ End V9.1 multipliers ═══

            if score >= MIN_SCORE:
                atr_v = _safe(last.get("atr"))
                st_v = _safe(last.get("supertrend"))

                # Stop احترافي: أعلى من (Supertrend) أو (-2 ATR)
                stop_atr = last_close - 2 * atr_v if atr_v > 0 else last_close * 0.97
                if st_v > 0 and st_v < last_close:
                    stop = round(max(stop_atr, st_v), 2)
                else:
                    stop = round(stop_atr, 2)

                # Targets: ATR-based + Fibonacci extension
                t1 = round(last_close + 2 * atr_v, 2) if atr_v > 0 else round(last_close * 1.04, 2)
                t2 = round(last_close + 3.5 * atr_v, 2) if atr_v > 0 else round(last_close * 1.07, 2)

                # Risk/Reward — يستخدم T2 (الهدف النهائي) لنسبة أكثر واقعية
                # T1 يعطي 1:1 دائماً بسبب تصميم ATR، T2 يعطي نسبة حقيقية متنوعة
                risk = last_close - stop
                reward_t1 = t1 - last_close
                reward_t2 = t2 - last_close
                # نسبة 1:X حيث X = متوسط المكافأة المرجحة (T1 وزن 60%, T2 وزن 40%)
                avg_reward = 0.6 * reward_t1 + 0.4 * reward_t2
                rr = round(avg_reward / risk, 2) if risk > 0 else 0

                # Expected Value — يستخدم الأهداف والوقف الفعليين للسهم
                target_pct = (t1 - last_close) / last_close * 100
                stop_pct = (stop - last_close) / last_close * 100

                if ml_prob is not None:
                    # ML متاح — استخدم احتماله الفعلي
                    ev_pct = ml_prob * target_pct + (1 - ml_prob) * stop_pct
                else:
                    # تقدير الاحتمال من score و signal confluence
                    # كلما زادت الإشارات الفعّالة وارتفع ADX، زاد احتمال النجاح
                    adx_v = _safe(last.get("adx"), 20)
                    confluence_boost = min(len(signals) * 0.02, 0.15)  # حتى +15%
                    adx_boost = min((adx_v - 20) / 100, 0.15) if adx_v > 20 else 0
                    est_prob = min(0.45 + score * 0.02 + confluence_boost + adx_boost, 0.72)
                    ev_pct = est_prob * target_pct + (1 - est_prob) * stop_pct

                candidates.append({
                    "ticker": code,
                    "close": round(last_close, 2),
                    "change": round(chg, 2),
                    "score": score,
                    "base_score": round(base_score, 2),  # V9.1: score قبل multipliers
                    "ml_probability": round(ml_prob, 3) if ml_prob is not None else None,
                    "expected_value_pct": round(ev_pct, 2),
                    "risk_reward": rr,
                    "reasons": reasons,
                    "signals": signals,
                    "sector": sec,
                    "rsi": round(_safe(last.get("rsi"), 50), 1),
                    "adx": round(_safe(last.get("adx"), 20), 1),
                    "mfi": round(_safe(last.get("mfi"), 50), 1),
                    "cmf": round(_safe(last.get("cmf")), 3),
                    "stoch_rsi": round(_safe(last.get("stoch_rsi_k"), 50), 1),
                    "fib_pos": round(_safe(last.get("fib_pos"), 0.5), 2),
                    "rs_vs_tasi": round(_safe(last.get("rs_vs_tasi"), 1.0), 3),
                    "volume_ratio": round(_safe(last.get("Volume")) / max(_safe(last.get("vol_ma20")), 1), 1),
                    "vwap_diff": round((last_close - _safe(last.get("vwap"), last_close)) /
                                       max(_safe(last.get("vwap")), 1) * 100, 1),
                    "weekly_trend": "صاعد" if _safe(last.get("weekly_trend")) == 1 else "هابط",
                    "supertrend_dir": int(_safe(last.get("supertrend_dir"))),
                    "stop": stop,
                    "target1": t1,
                    "target2": t2,
                    "atr": round(atr_v, 2),
                    "feature_snapshot": features,  # لاستخدامه لاحقاً في ML training
                    # V9.1 additions
                    "mtf_aligned": mtf_info.get("aligned_count", 0),
                    "mtf_available": mtf_info.get("available_count", 0),
                    "mtf_multiplier": mtf_info.get("mtf_multiplier", 1.0),
                    "mtf_daily_score": mtf_info.get("daily_score"),
                    "mtf_h4_score": mtf_info.get("h4_score"),
                    "mtf_h1_score": mtf_info.get("h1_score"),
                    "news_sentiment": news_info.get("label", "no_news"),
                    "news_score": news_info.get("score", 0),
                    "news_multiplier": news_info.get("multiplier", 1.0),
                    "news_headlines": news_info.get("headlines", [])[:2],
                    "news_reason": news_info.get("reason", ""),
                    "earnings_multiplier": earnings_info.get("multiplier", 1.0),
                    "earnings_message": earnings_info.get("message"),
                })
        except Exception as e:
            errors += 1
            log.debug(f"  {ticker}: {e}")

    # معالجة sector_summary
    for sec in sector_summary:
        s = sector_summary[sec]
        s["avg_change"] = round(s["total_change"] / max(s["count"], 1), 2)
        s["pct_gainers"] = round(s["gainers"] / max(s["count"], 1) * 100, 1)

    # الترتيب
    candidates.sort(key=lambda x: -x["score"])
    gainers.sort(key=lambda x: -x["change"])

    print(f"  ✓ {len(candidates)} مرشح | {errors} خطأ")

    return candidates[:TOP_N], gainers[:15], macro, sector_summary, stocks_data


# ════════════════════════════════════════════════
# تشغيل
# ════════════════════════════════════════════════
def run():
    print(f"\n{'━'*50}")
    print(f"  ماسح تداول V9 — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'━'*50}")

    # الأوزان
    weights = load_json(F_WEIGHTS, DEFAULT_WEIGHTS.copy())
    weights = {k: weights.get(k, v) for k, v in DEFAULT_WEIGHTS.items()}

    # التراكر
    tracker = load_json(F_TRACKER, None) or init_tracker()
    for sig in DEFAULT_WEIGHTS:
        if sig not in tracker["signal_accuracy"]:
            tracker["signal_accuracy"][sig] = {"triggered": 0, "hit": 0, "miss": 0, "rate": 0.0}

    weights = apply_decay(weights)

    # 1. تقييم الأمس
    print("\n  📋 تقييم توقعات الأمس...")
    try:
        eval_results, weights, tracker, eval_summary = evaluate_yesterday(weights, tracker)
        print(f"  → {eval_summary}")
    except Exception as e:
        print(f"  ⚠️ تخطي التقييم (أول تشغيل أو خطأ): {e}")
        eval_results, eval_summary = [], "لم يتم التقييم"

    # 2. تدريب ML (لو البيانات كافية)
    print("\n  🤖 تدريب نموذج ML...")
    try:
        ml_result = train_model(min_samples=100)
        print(f"  → {ml_result.get('status')}")
        if ml_result.get("status") == "success":
            m = ml_result["metrics"]
            print(f"  → AUC={m.get('roc_auc')} | Acc={m.get('accuracy')} | F1={m.get('f1')}")

            # استخدم feature_importance لتعديل الأوزان
            ml_weights = suggest_weights_from_importance()
            if ml_weights:
                # دمج: 70% الأوزان الحالية + 30% اقتراح ML
                for ind, ml_w in ml_weights.items():
                    if ind in weights:
                        weights[ind] = 0.7 * weights[ind] + 0.3 * ml_w
                weights = clamp_weights(weights)
                print(f"  → تم دمج اقتراحات ML في الأوزان")
    except Exception as e:
        print(f"  ⚠️ تخطي ML: {e}")

    # 3. المسح
    bl = tracker.get("blacklist", [])
    candidates, gainers, macro, sector_summary, stocks_data = scan_tasi(weights, bl)

    # 4. تحليل intermarket + sector flows
    print("\n  🔗 تحليل الارتباطات وتدفق السيولة...")
    intermarket = {
        "highly_correlated_pairs": [], "divergent_pairs": [],
        "sector_flows": {}, "leader_laggard": {}, "sector_rotation": [],
    }
    try:
        prev_sector_flows = load_json(F_SECTOR_PREV, None)
        intermarket = build_intermarket_summary(stocks_data, TICKER_SECTOR, prev_sector_flows)
        # حفظ flows الحالية كـ prev للغد
        save_json(F_SECTOR_PREV, intermarket["sector_flows"])
        print(f"  ✓ أزواج مرتبطة: {len(intermarket['highly_correlated_pairs'])}")
        print(f"  ✓ أزواج متباعدة (فرص catch-up): {len(intermarket['divergent_pairs'])}")
        print(f"  ✓ دوران قطاعات: {len(intermarket['sector_rotation'])}")
    except Exception as e:
        print(f"  ⚠️ فشل تحليل intermarket: {e}")

    # 5. حفظ النتائج
    save_json(F_CANDIDATES, {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "candidates": candidates,
        "gainers": gainers,
        "macro": macro,
        "sector_summary": sector_summary,
        "intermarket": intermarket,
        "eval_summary": eval_summary,
        "eval_results": eval_results,
        "weights": weights,
        "signal_accuracy": tracker.get("signal_accuracy", {}),
        "ml_metrics": load_json(BASE / "ml_metrics.json", {}),
        "feature_importance": load_json(BASE / "feature_importance.json", {}),
    })
    save_json(F_WEIGHTS, weights)
    save_json(F_TRACKER, tracker)

    # 6. حفظ history — لكن بدون تكرار اليوم!
    history = load_json(F_HISTORY, [])
    today_str = datetime.now().strftime("%Y-%m-%d")
    # إذا كان اليوم موجوداً، حدّثه بدل الإضافة (يمنع bug 0/0 عند التشغيل المتكرر)
    found = False
    for entry in history:
        if entry.get("date") == today_str:
            entry["predictions"] = candidates
            found = True
            break
    if not found:
        history.append({"date": today_str, "predictions": candidates})
    history = history[-120:]
    save_json(F_HISTORY, history)

    print(f"\n  ✅ اكتمل — {len(candidates)} مرشح | {len(gainers)} مرتفع")
    return candidates, gainers, macro, sector_summary, intermarket


if __name__ == "__main__":
    run()
