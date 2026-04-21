# -*- coding: utf-8 -*-
"""
طبقة مصادر البيانات الموحدة — V9
====================================
تُصلح مشكلة MultiIndex في yfinance الجديد وتدعم EOD اختيارياً.

الاستخدام:
  - افتراضياً: yfinance (مجاني)
  - إذا وُجد EODHD_API_KEY: يستخدم EOD Historical Data (أدق + أسرع)

الدوال:
  fetch_ohlcv(ticker, period_days=180) -> DataFrame
  fetch_ohlcv_batch(tickers, period_days=180) -> dict[ticker, DataFrame]
  fetch_macro() -> dict  (يُصلح bug الـ Series في yfinance)
"""
import pandas as pd
import numpy as np
import yfinance as yf
import os, time, logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# EODHD key اختياري
EODHD_KEY = os.environ.get("EODHD_API_KEY", "").strip()
USE_EODHD = bool(EODHD_KEY)

# cache في الذاكرة لتقليل طلبات متكررة داخل نفس المسح
_MACRO_CACHE = {"data": None, "ts": 0}
_CACHE_TTL = 600  # 10 دقائق


def _safe_float(val):
    """تحويل آمن لأي شيء إلى float — يُصلح bug الـ Series من yfinance الجديد."""
    if val is None:
        return None
    if isinstance(val, pd.Series):
        val = val.iloc[-1] if len(val) > 0 else None
    if isinstance(val, pd.DataFrame):
        if val.empty:
            return None
        val = val.iloc[-1, 0]
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _flatten_yf_columns(df):
    """تسطيح MultiIndex columns التي يُرجعها yfinance الجديد حتى لرمز واحد."""
    if isinstance(df.columns, pd.MultiIndex):
        # إذا كان MultiIndex فيه مستوى واحد للأعمدة (Open, High, ...)، خذ المستوى 0
        df.columns = df.columns.get_level_values(0)
    return df


# ────────────────────────────────────────────
# EODHD بديل (اختياري)
# ────────────────────────────────────────────
def _eodhd_fetch_one(symbol_eod, period_days=180):
    """جلب سهم واحد من EODHD. symbol_eod مثلاً: 2222.SR"""
    import urllib.request, json as _json
    end = datetime.now().date()
    start = end - timedelta(days=period_days + 30)
    url = (f"https://eodhd.com/api/eod/{symbol_eod}?"
           f"from={start}&to={end}&period=d&fmt=json&api_token={EODHD_KEY}")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = _json.loads(r.read().decode())
        if not data or isinstance(data, dict) and data.get("error"):
            return pd.DataFrame()
        df = pd.DataFrame(data)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "adjusted_close": "Adj Close", "volume": "Volume"
        })
        return df
    except Exception as e:
        log.debug(f"EODHD {symbol_eod}: {e}")
        return pd.DataFrame()


# ────────────────────────────────────────────
# واجهة موحدة: ohlcv لسهم واحد
# ────────────────────────────────────────────
def fetch_ohlcv(ticker, period_days=180):
    """
    ticker: بصيغة "2222.SR" أو "^GSPC" إلخ
    period_days: عدد الأيام التقويمية
    يُرجع DataFrame بأعمدة: Open, High, Low, Close, Volume
    """
    if USE_EODHD and ticker.endswith(".SR"):
        df = _eodhd_fetch_one(ticker, period_days)
        if not df.empty and len(df) >= 50:
            return df

    # fallback: yfinance
    try:
        df = yf.download(ticker, period=f"{period_days}d", progress=False, auto_adjust=False)
        df = _flatten_yf_columns(df)
        return df
    except Exception as e:
        log.warning(f"yf {ticker}: {e}")
        return pd.DataFrame()


def fetch_ohlcv_batch(tickers, period_days=180, batch_size=30):
    """
    جلب مجموعة أسهم. يستخدم EODHD واحد-واحد إذا فُعِّل، وإلا batch من yfinance.
    يُرجع dict: {ticker: DataFrame}
    """
    result = {}

    if USE_EODHD:
        # EODHD واحد-واحد (معدل الطلب ~5 طلب/ثانية للحساب الأساسي)
        for t in tickers:
            df = _eodhd_fetch_one(t, period_days)
            if not df.empty:
                result[t] = df
            time.sleep(0.05)
        return result

    # yfinance batch — نتعامل مع MultiIndex بعناية
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            data = yf.download(batch, period=f"{period_days}d", progress=False,
                               group_by="ticker", threads=True, auto_adjust=False)
            time.sleep(0.4)
            for t in batch:
                try:
                    if len(batch) == 1:
                        df = _flatten_yf_columns(data.copy())
                    else:
                        if t in data.columns.get_level_values(0):
                            df = data[t].copy()
                        else:
                            continue
                    if not df.empty:
                        df = df.dropna(subset=["Close"])
                        if len(df) >= 50:
                            result[t] = df
                except Exception as e:
                    log.debug(f"{t} in batch: {e}")
        except Exception as e:
            log.warning(f"Batch {i}: {e}")
    return result


# ────────────────────────────────────────────
# الماكرو — يُصلح bug الإصدار الجديد من yfinance
# ────────────────────────────────────────────
def fetch_macro(use_cache=True):
    """
    يُرجع dict كامل بـ:
      oil, oil_chg, gold, gold_chg, sp500, sp500_chg,
      vix, vix_level, us10y, us10y_chg, dxy, dxy_chg,
      tasi_index, tasi_chg
    """
    now = time.time()
    if use_cache and _MACRO_CACHE["data"] and (now - _MACRO_CACHE["ts"]) < _CACHE_TTL:
        return _MACRO_CACHE["data"]

    macro = {
        "oil": "N/A", "oil_chg": 0.0,
        "gold": "N/A", "gold_chg": 0.0,
        "sp500": "N/A", "sp500_chg": 0.0,
        "vix": "N/A", "vix_level": 0.0,
        "us10y": "N/A", "us10y_chg": 0.0,
        "dxy": "N/A", "dxy_chg": 0.0,
        "tasi_index": "N/A", "tasi_chg": 0.0,
        "fetch_errors": [],
    }

    symbols = {
        "CL=F":   ("oil",        "oil_chg",    "$"),
        "GC=F":   ("gold",       "gold_chg",   "$"),
        "^GSPC":  ("sp500",      "sp500_chg",  ""),
        "^VIX":   ("vix",        "vix_level",  "vix"),
        "^TNX":   ("us10y",      "us10y_chg",  "%"),
        "DX-Y.NYB": ("dxy",      "dxy_chg",    ""),
        "^TASI.SR": ("tasi_index", "tasi_chg", ""),
    }

    for sym, (name_key, chg_key, fmt) in symbols.items():
        try:
            df = yf.download(sym, period="5d", progress=False, auto_adjust=False)
            df = _flatten_yf_columns(df)
            if df.empty or "Close" not in df.columns or len(df) < 2:
                macro["fetch_errors"].append(sym)
                continue

            last_val = _safe_float(df["Close"].iloc[-1])
            prev_val = _safe_float(df["Close"].iloc[-2])
            if last_val is None or prev_val is None or prev_val == 0:
                macro["fetch_errors"].append(sym)
                continue

            chg = round((last_val - prev_val) / prev_val * 100, 2)

            if fmt == "vix":
                macro[name_key] = f"{last_val:.1f}"
                macro[chg_key] = round(last_val, 1)
            elif fmt == "%":
                macro[name_key] = f"{last_val:.2f}%"
                macro[chg_key] = chg
            elif fmt == "$":
                macro[name_key] = f"${last_val:.2f}"
                macro[chg_key] = chg
            else:
                macro[name_key] = f"{last_val:,.0f}" if last_val > 100 else f"{last_val:.2f}"
                macro[chg_key] = chg

        except Exception as e:
            log.warning(f"Macro {sym}: {e}")
            macro["fetch_errors"].append(sym)

    _MACRO_CACHE["data"] = macro
    _MACRO_CACHE["ts"] = now
    return macro


if __name__ == "__main__":
    # اختبار سريع
    logging.basicConfig(level=logging.INFO)
    print("Testing macro fetch...")
    m = fetch_macro(use_cache=False)
    for k, v in m.items():
        print(f"  {k}: {v}")
    print(f"\nUSE_EODHD: {USE_EODHD}")
