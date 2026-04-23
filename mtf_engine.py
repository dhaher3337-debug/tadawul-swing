# -*- coding: utf-8 -*-
"""
Multi-Timeframe Confirmation — V9.1
========================================
يُؤكّد إشارات الشراء عبر 3 فريمات زمنية:
  - Daily (اليومي) — الأساسي (موجود في scanner_v9)
  - 4-Hour — تأكيد الاتجاه متوسط المدى
  - 1-Hour — تأكيد الزخم قصير المدى

الإشارة الصالحة = 2 أو 3 من الفريمات متوافقة صعوداً.

الاستخدام:
  from mtf_engine import check_mtf_alignment
  result = check_mtf_alignment(ticker_symbol)
  # {'aligned': True, 'score': 2, 'details': {...}}

يُستخدم كـ multiplier للـ score في scanner_v9:
  - 3/3 فريمات صاعدة → ×1.3 (تعزيز قوي)
  - 2/3 فريمات صاعدة → ×1.1 (تعزيز خفيف)
  - 1/3 فريمات صاعدة → ×0.7 (تخفيض)
  - 0/3 فريمات صاعدة → ×0.4 (تخفيض قوي)

القيود:
  - yfinance يقدم فريمات داخلية لآخر 60 يوم فقط (للـ 4h و 1h)
  - في السوق السعودي، قد تكون بعض الأسهم محدودة الفريمات الداخلية
  - fallback ذكي: إذا تعذر جلب الفريم، يُعتبر "محايد"
"""
import pandas as pd
import numpy as np
import yfinance as yf
import logging
from datetime import datetime

log = logging.getLogger(__name__)


def _fetch_intraday(ticker, interval, period="60d"):
    """
    جلب بيانات داخلية (intraday). 
    interval: "1h", "4h", "30m", "15m"
    """
    try:
        df = yf.download(
            ticker, period=period, interval=interval,
            progress=False, auto_adjust=False,
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"])
        return df
    except Exception as e:
        log.debug(f"Intraday fetch {ticker} {interval}: {e}")
        return pd.DataFrame()


def _fetch_4h_from_1h(ticker):
    """
    yfinance لا يدعم 4h مباشرة — نبنيه من 1h.
    """
    df_1h = _fetch_intraday(ticker, "1h", period="60d")
    if df_1h.empty or len(df_1h) < 20:
        return pd.DataFrame()
    
    try:
        # Resample إلى 4h
        df_4h = df_1h.resample("4H").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }).dropna()
        return df_4h
    except Exception as e:
        log.debug(f"4h resample {ticker}: {e}")
        return pd.DataFrame()


def _compute_trend_score(df):
    """
    يحسب "درجة الاتجاه" للفريم الواحد: قيمة بين -1 (هابط قوي) و +1 (صاعد قوي).
    
    المعايير (موزونة):
      - EMA 9 vs EMA 21 (0.25)
      - Close vs SMA 20 (0.20)
      - MACD histogram إيجابي (0.20)
      - RSI > 50 (0.15)
      - Higher highs في آخر 5 شموع (0.20)
    """
    if df.empty or len(df) < 25:
        return 0.0
    
    close = df["Close"]
    high = df["High"]
    
    # 1. EMA 9 vs EMA 21
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema_score = 1 if ema9.iloc[-1] > ema21.iloc[-1] else -1
    
    # 2. Close vs SMA 20
    sma20 = close.rolling(20).mean()
    if pd.isna(sma20.iloc[-1]):
        sma_score = 0
    else:
        dist_pct = (close.iloc[-1] - sma20.iloc[-1]) / sma20.iloc[-1] * 100
        sma_score = max(-1, min(1, dist_pct / 3))  # -3% → -1, +3% → +1
    
    # 3. MACD histogram
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_sig = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = (macd_line - macd_sig).iloc[-1]
    macd_score = 1 if macd_hist > 0 else -1
    
    # 4. RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta).where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_val = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
    rsi_score = max(-1, min(1, (rsi_val - 50) / 20))  # 30 → -1, 70 → +1
    
    # 5. Higher highs in last 5 bars
    recent_highs = high.tail(5)
    if len(recent_highs) >= 5:
        hh_count = sum(
            1 for i in range(1, len(recent_highs))
            if recent_highs.iloc[i] > recent_highs.iloc[i-1]
        )
        hh_score = (hh_count - 2) / 2  # 4 HH → +1, 0 HH → -1
    else:
        hh_score = 0
    
    # Weighted sum
    total = (
        0.25 * ema_score +
        0.20 * sma_score +
        0.20 * macd_score +
        0.15 * rsi_score +
        0.20 * hh_score
    )
    return round(total, 3)


def check_mtf_alignment(ticker):
    """
    الدالة الرئيسية: تفحص 3 فريمات وترجع ملخص.
    
    Returns:
        {
            "aligned_count": int (0-3),
            "mtf_multiplier": float (0.4 - 1.3),
            "daily_score": float or None,  # None إذا لم تتوفر بيانات
            "h4_score": float or None,
            "h1_score": float or None,
            "details": str,
        }
    """
    result = {
        "aligned_count": 0,
        "mtf_multiplier": 1.0,
        "daily_score": None,
        "h4_score": None,
        "h1_score": None,
        "details": "",
    }
    
    # Daily (30 يوم يكفي لحساب SMA20 + EMA21)
    df_d = _fetch_intraday(ticker, "1d", period="60d")
    if not df_d.empty and len(df_d) >= 25:
        result["daily_score"] = _compute_trend_score(df_d)
    
    # 1H (60 يوم هو الحد الأقصى لـ yfinance)
    df_1h = _fetch_intraday(ticker, "1h", period="60d")
    if not df_1h.empty and len(df_1h) >= 25:
        result["h1_score"] = _compute_trend_score(df_1h)
    
    # 4H (من 1h بالـ resample)
    df_4h = _fetch_4h_from_1h(ticker)
    if not df_4h.empty and len(df_4h) >= 25:
        result["h4_score"] = _compute_trend_score(df_4h)
    
    # احسب aligned_count: كم فريم صاعد (score > 0.2)
    THRESHOLD = 0.2
    aligned = 0
    available = 0
    positive_signals = []
    negative_signals = []
    
    for tf_name, score in [("D", result["daily_score"]),
                           ("4H", result["h4_score"]),
                           ("1H", result["h1_score"])]:
        if score is None:
            continue
        available += 1
        if score > THRESHOLD:
            aligned += 1
            positive_signals.append(f"{tf_name}:+{score:.2f}")
        elif score < -THRESHOLD:
            negative_signals.append(f"{tf_name}:{score:.2f}")
    
    result["aligned_count"] = aligned
    result["available_count"] = available
    
    # multiplier: يعتمد على نسبة aligned/available
    if available == 0:
        result["mtf_multiplier"] = 1.0  # محايد — لا بيانات
        result["details"] = "⚠️ بيانات MTF غير متاحة"
    else:
        ratio = aligned / available
        if ratio >= 0.99:  # 3/3 أو 2/2
            result["mtf_multiplier"] = 1.3
        elif ratio >= 0.65:  # 2/3
            result["mtf_multiplier"] = 1.1
        elif ratio >= 0.33:  # 1/3
            result["mtf_multiplier"] = 0.7
        else:  # 0/3
            result["mtf_multiplier"] = 0.4
        
        if positive_signals:
            result["details"] = f"MTF {aligned}/{available} صاعد: " + ", ".join(positive_signals)
        elif negative_signals:
            result["details"] = f"MTF هابط: " + ", ".join(negative_signals)
        else:
            result["details"] = f"MTF محايد {available} فريمات"
    
    return result


def check_mtf_batch(tickers, max_workers=5):
    """
    جلب MTF لعدة أسهم (بالتتابع — yfinance لا يحب التوازي العالي مع intraday).
    Returns: dict {ticker: mtf_result}
    """
    import time
    results = {}
    for t in tickers:
        try:
            results[t] = check_mtf_alignment(t)
            time.sleep(0.3)  # تجنب rate-limiting
        except Exception as e:
            log.debug(f"MTF batch {t}: {e}")
            results[t] = {
                "aligned_count": 0,
                "mtf_multiplier": 1.0,
                "daily_score": None,
                "h4_score": None,
                "h1_score": None,
                "details": f"خطأ: {e}",
                "available_count": 0,
            }
    return results


if __name__ == "__main__":
    # اختبار سريع
    logging.basicConfig(level=logging.INFO)
    print("اختبار MTF لأرامكو...")
    r = check_mtf_alignment("2222.SR")
    print(f"  Daily: {r['daily_score']}")
    print(f"  4H: {r['h4_score']}")
    print(f"  1H: {r['h1_score']}")
    print(f"  Aligned: {r['aligned_count']}/{r['available_count']}")
    print(f"  Multiplier: ×{r['mtf_multiplier']}")
    print(f"  Details: {r['details']}")
