# -*- coding: utf-8 -*-
"""
Earnings Calendar Filter — V9.1
=====================================
يجلب تقويم إعلان الأرباح للأسهم السعودية ويُعيد:
  - هل السهم ضمن نافذة إعلان أرباح (2-3 أيام قبل الإعلان)؟
  - إذا نعم: تخفيض الثقة (×0.6) لأن التقلبات ستكون كبيرة

المصادر:
  1. Argaam calendar (أولوية)
  2. yfinance earnings_dates (احتياطي — غير موثوق للسعودي)
  3. حساب تقديري من التواريخ التاريخية (fallback أخير)

الاستراتيجية:
  - تحديث أسبوعي (cache لـ 7 أيام)
  - فقط آخر 30 يوم + القادمة 15 يوم
  - السوق السعودي: معظم الشركات تُعلن ربع سنوي في أواخر:
    * Q1: مايو
    * Q2: أغسطس
    * Q3: نوفمبر
    * Q4: فبراير/مارس
"""
import re
import json
import time
import logging
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
F_EARNINGS_CACHE = BASE / "earnings_calendar.json"
CACHE_DAYS = 7

USER_AGENT = "Mozilla/5.0 (compatible; TadawulV9/1.0)"

# نوافذ إعلانات الأرباح المتوقعة للسوق السعودي (تقريبياً)
# الشركات السعودية تُعلن خلال 30-45 يوم بعد نهاية الربع
TYPICAL_EARNINGS_WINDOWS = {
    # شهر → (يوم_البداية, يوم_النهاية) تقريبياً
    1: None,
    2: (10, 28),   # فبراير — Q4 السابقة
    3: (1, 20),    # مارس — Q4 السابقة (تأخّر)
    4: None,
    5: (5, 25),    # مايو — Q1
    6: None,
    7: None,
    8: (5, 25),    # أغسطس — Q2
    9: None,
    10: None,
    11: (5, 25),   # نوفمبر — Q3
    12: None,
}


def _fetch_url(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.debug(f"fetch {url}: {e}")
        return None


def is_in_earnings_window(date=None):
    """
    هل اليوم ضمن نافذة متوقعة لإعلانات الأرباح؟
    (fallback عندما لا نستطيع جلب التواريخ الفعلية)
    
    Returns: bool
    """
    date = date or datetime.now()
    window = TYPICAL_EARNINGS_WINDOWS.get(date.month)
    if window is None:
        return False
    start_day, end_day = window
    return start_day <= date.day <= end_day


def is_stock_in_earnings_window(ticker, earnings_dates, days_before=3):
    """
    هل السهم له إعلان أرباح خلال days_before يوم؟
    
    Args:
        ticker: كود السهم
        earnings_dates: dict {ticker: [list of ISO dates]}
        days_before: عدد الأيام قبل الإعلان لاعتبار "نافذة خطر"
    
    Returns:
        (in_window: bool, days_until: int or None, announce_date: str or None)
    """
    dates = earnings_dates.get(ticker, [])
    if not dates:
        return False, None, None
    
    now = datetime.now().date()
    for date_str in dates:
        try:
            ann_date = datetime.fromisoformat(date_str).date()
        except Exception:
            continue
        days_until = (ann_date - now).days
        if 0 <= days_until <= days_before:
            return True, days_until, date_str
    
    return False, None, None


def fetch_earnings_from_yfinance(tickers, batch_size=20):
    """
    يحاول جلب تواريخ الأرباح من yfinance.
    غير موثوق بشكل كبير للسوق السعودي، لكن نجرّب.
    
    Returns: dict {ticker: [iso_dates]}
    """
    import yfinance as yf
    result = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            # محاولة calendar
            cal = t.calendar
            if cal is not None and not cal.empty:
                earnings_dates = cal.get("Earnings Date")
                if earnings_dates is not None:
                    # يأتي أحياناً كسلسلة تاريخ واحد أو قائمة
                    if hasattr(earnings_dates, "__iter__"):
                        dates = [d.isoformat() if hasattr(d, "isoformat") else str(d)
                                 for d in earnings_dates]
                    else:
                        dates = [str(earnings_dates)]
                    result[ticker.replace(".SR", "")] = dates
            time.sleep(0.15)
        except Exception as e:
            log.debug(f"earnings {ticker}: {e}")
    return result


def estimate_earnings_from_pattern(ticker_codes):
    """
    تقدير تواريخ الأرباح القادمة بناءً على نمط السوق السعودي.
    كل سهم: نضع تاريخ افتراضي في نهاية كل ربع.
    
    Returns: dict {ticker: [iso_dates]}
    """
    result = {}
    now = datetime.now()
    
    # تواريخ متوقعة للإعلانات القادمة (تقريبياً في منتصف نافذة كل ربع)
    current_year = now.year
    expected_dates = []
    
    # Q4 previous year (Feb-Mar)
    expected_dates.append(datetime(current_year, 2, 20))
    # Q1 (May)
    expected_dates.append(datetime(current_year, 5, 15))
    # Q2 (Aug)
    expected_dates.append(datetime(current_year, 8, 15))
    # Q3 (Nov)
    expected_dates.append(datetime(current_year, 11, 15))
    # Q4 next year (Feb-Mar)
    expected_dates.append(datetime(current_year + 1, 2, 20))
    
    # احتفظ فقط بالتواريخ القادمة
    future_dates = [d for d in expected_dates if d >= now - timedelta(days=10)]
    future_dates = future_dates[:3]  # آخر 3 متوقعة
    
    iso_dates = [d.date().isoformat() for d in future_dates]
    
    for code in ticker_codes:
        result[code] = iso_dates.copy()
    
    return result


def get_earnings_calendar(ticker_codes, force_refresh=False):
    """
    الدالة الرئيسية: يرجع تقويم الأرباح مع cache.
    
    Args:
        ticker_codes: set/list of codes مثل {"2222", "1120", ...}
    
    Returns: {
        "fetched_at": "...",
        "method": "yfinance" | "estimated",
        "earnings": {"2222": ["2026-05-10", ...], ...}
    }
    """
    # Cache check
    if not force_refresh and F_EARNINGS_CACHE.exists():
        try:
            with open(F_EARNINGS_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            fetched = datetime.fromisoformat(cached.get("fetched_at", ""))
            age = datetime.now() - fetched
            if age < timedelta(days=CACHE_DAYS):
                log.info(f"Using cached earnings ({len(cached.get('earnings', {}))} tickers)")
                return cached
        except Exception:
            pass
    
    # أولاً: جرّب yfinance (قد لا يعطينا شيئاً للسعودي)
    log.info("Attempting yfinance earnings fetch...")
    tickers_sr = [f"{c}.SR" for c in ticker_codes]
    yf_data = {}
    try:
        yf_data = fetch_earnings_from_yfinance(tickers_sr[:20])  # جرّب أول 20 فقط للسرعة
    except Exception as e:
        log.warning(f"yfinance earnings: {e}")
    
    # إذا يfinance أعطى بيانات مفيدة (>30% من الأسهم) — استخدمه
    if len(yf_data) >= len(ticker_codes) * 0.3:
        method = "yfinance"
        earnings = yf_data
        # أضف estimate للباقين
        estimated = estimate_earnings_from_pattern(ticker_codes)
        for code, dates in estimated.items():
            if code not in earnings:
                earnings[code] = dates
    else:
        # fallback: استخدم النمط العام
        log.info("Falling back to pattern-based estimation")
        method = "estimated"
        earnings = estimate_earnings_from_pattern(ticker_codes)
    
    result = {
        "fetched_at": datetime.now().isoformat(),
        "method": method,
        "earnings": earnings,
        "tickers_count": len(earnings),
    }
    
    try:
        F_EARNINGS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(F_EARNINGS_CACHE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"save cache: {e}")
    
    return result


def get_earnings_multiplier(ticker, earnings_data, days_before=3):
    """
    يرجع multiplier للسهم بناءً على قرب الأرباح.
    - خلال 0-1 يوم من الإعلان: ×0.4 (خطر كبير، تجنّب)
    - خلال 2 يوم: ×0.6
    - خلال 3 يوم: ×0.8
    - بعد 4+ يوم: ×1.0 (طبيعي)
    
    Returns: (multiplier, message)
    """
    earnings = earnings_data.get("earnings", {})
    in_window, days_until, date = is_stock_in_earnings_window(
        ticker, earnings, days_before=days_before
    )
    
    if not in_window:
        return 1.0, None
    
    if days_until == 0:
        return 0.3, f"⚠️ إعلان أرباح اليوم ({date})"
    elif days_until == 1:
        return 0.4, f"⚠️ إعلان أرباح غداً ({date})"
    elif days_until == 2:
        return 0.6, f"⚠️ إعلان أرباح بعد يومين ({date})"
    elif days_until == 3:
        return 0.8, f"ℹ️ إعلان أرباح خلال 3 أيام ({date})"
    else:
        return 1.0, None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # اختبار
    test_codes = ["2222", "1120", "2010", "4190"]
    data = get_earnings_calendar(test_codes, force_refresh=True)
    print(f"\nMethod: {data['method']}")
    print(f"Tickers: {data['tickers_count']}")
    for ticker, dates in data["earnings"].items():
        mult, msg = get_earnings_multiplier(ticker, data)
        print(f"  {ticker}: mult={mult} | {msg or 'OK'} | dates={dates[:2]}")
