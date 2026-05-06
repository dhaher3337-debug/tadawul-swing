# -*- coding: utf-8 -*-
"""
Data Freshness Check - V9.2.1
==============================
🎯 الهدف: منع توليد تقارير فاسدة عندما تكون بيانات yfinance قديمة

السيناريو الذي حدث في الإثنين 4 مايو:
  - السوق أغلق 3 مساءً سعودي (12 UTC الإثنين)
  - النظام شغّل 5 ص سعودي الثلاثاء (02 UTC)
  - yfinance لم يحدّث بيانات الإثنين بعد
  - نتيجة: 9 بنوك في "أعلى الرابحين" بـ 0.00%
  - النظام يبدو وكأنه يعمل لكن البيانات وهمية

الحل: قبل توليد التقرير، نتحقق:
  1. هل آخر تاريخ في البيانات = آخر يوم تداول متوقع؟
  2. هل عدد كبير من الأسهم تغيّرها = 0.00% بالضبط؟
"""
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# الأيام التي السوق السعودي يكون فيها مفتوح (Mon=0...Sun=6)
TRADING_WEEKDAYS = {6, 0, 1, 2, 3}  # Sun, Mon, Tue, Wed, Thu


def get_expected_last_trading_day(now=None):
    """
    احسب آخر يوم تداول متوقع بناءً على وقت الـ run.
    
    إذا كانت الساعة < 6 UTC: آخر إغلاق هو يوم أمس
    إذا كانت الساعة >= 12 UTC: قد يكون اليوم أو أمس
    
    للسوق السعودي: 7-12 UTC (10 ص-3 م سعودي)
    """
    if now is None:
        now = datetime.utcnow()
    
    # أمس بالنسبة لوقت الـ run
    yesterday = now - timedelta(days=1)
    
    # ابحث للخلف عن آخر يوم تداول
    # max 7 أيام للحماية من loops لانهائية
    candidate = yesterday
    for _ in range(7):
        if candidate.weekday() in TRADING_WEEKDAYS:
            return candidate.strftime("%Y-%m-%d")
        candidate -= timedelta(days=1)
    
    # ما يفترض نوصل هنا
    return yesterday.strftime("%Y-%m-%d")


def check_data_freshness(stocks_data, expected_date=None, min_fresh_pct=70):
    """
    فحص حداثة بيانات الأسهم.
    
    Args:
        stocks_data: dict {ticker: DataFrame}
        expected_date: التاريخ المتوقع لآخر إغلاق (YYYY-MM-DD)
        min_fresh_pct: الحد الأدنى لنسبة الأسهم بآخر تاريخ متوقع
    
    Returns:
        dict {
            "is_fresh": bool,
            "expected_date": str,
            "actual_dates": dict {date: count},
            "fresh_pct": float,
            "warnings": [str],
        }
    """
    if expected_date is None:
        expected_date = get_expected_last_trading_day()
    
    if not stocks_data:
        return {
            "is_fresh": False,
            "expected_date": expected_date,
            "actual_dates": {},
            "fresh_pct": 0,
            "warnings": ["لا توجد بيانات أسهم"],
        }
    
    # احسب توزيع آخر تاريخ في كل DataFrame
    from collections import Counter
    last_dates = Counter()
    zero_change_count = 0
    total_with_data = 0
    
    for ticker, df in stocks_data.items():
        if df is None or df.empty:
            continue
        total_with_data += 1
        
        try:
            # آخر تاريخ
            last_dt = df.index[-1]
            if hasattr(last_dt, 'strftime'):
                last_date = last_dt.strftime("%Y-%m-%d")
            else:
                last_date = str(last_dt)[:10]
            last_dates[last_date] += 1
            
            # هل التغير = 0%؟ (دلالة أن بيانات اليوم لم تتحدث)
            if len(df) >= 2:
                prev_close = df["Close"].iloc[-2] if "Close" in df.columns else None
                last_close = df["Close"].iloc[-1] if "Close" in df.columns else None
                if prev_close and last_close:
                    pct_change = abs((last_close - prev_close) / prev_close * 100)
                    if pct_change < 0.001:  # تقريباً صفر
                        zero_change_count += 1
        except Exception as e:
            log.debug(f"freshness check error for {ticker}: {e}")
    
    # احسب نسبة الأسهم بالتاريخ المتوقع
    fresh_count = last_dates.get(expected_date, 0)
    fresh_pct = (fresh_count / total_with_data * 100) if total_with_data > 0 else 0
    
    # الإنذارات
    warnings = []
    if fresh_pct < min_fresh_pct:
        warnings.append(
            f"⚠️ فقط {fresh_pct:.0f}% من الأسهم لها بيانات بتاريخ {expected_date} "
            f"(الحد الأدنى: {min_fresh_pct}%)"
        )
    
    if total_with_data > 0:
        zero_pct = zero_change_count / total_with_data * 100
        if zero_pct > 30:
            warnings.append(
                f"⚠️ {zero_pct:.0f}% من الأسهم تغيّرها = 0.00% بالضبط "
                f"(دلالة قوية على بيانات قديمة)"
            )
    
    is_fresh = (fresh_pct >= min_fresh_pct) and len(warnings) == 0
    
    return {
        "is_fresh": is_fresh,
        "expected_date": expected_date,
        "actual_dates": dict(last_dates),
        "fresh_pct": round(fresh_pct, 1),
        "zero_change_pct": round(zero_change_count / total_with_data * 100, 1) if total_with_data > 0 else 0,
        "total_stocks": total_with_data,
        "warnings": warnings,
    }


def should_abort_run(freshness_result, strict_mode=True):
    """
    قرار: هل نوقف الـ run بسبب بيانات قديمة؟
    
    strict_mode=True (افتراضي):
        أوقف إذا is_fresh = False
    strict_mode=False:
        أكمل لكن مع تحذيرات في التقرير
    """
    if not strict_mode:
        return False
    
    if not freshness_result["is_fresh"]:
        return True
    
    return False


if __name__ == "__main__":
    # اختبار
    import sys
    print("=" * 60)
    print("Data Freshness Check - Test")
    print("=" * 60)
    print(f"اليوم متوقع: {get_expected_last_trading_day()}")
    print()
    
    # سيناريو وهمي
    import pandas as pd
    test_data = {
        "2222.SR": pd.DataFrame({
            "Close": [27.0, 27.5],
            "High": [27.5, 28.0],
            "Low": [26.5, 27.0],
        }, index=pd.DatetimeIndex(["2026-05-04", "2026-05-04"])),  # نفس التاريخ!
    }
    
    result = check_data_freshness(test_data, expected_date="2026-05-04")
    print(f"is_fresh: {result['is_fresh']}")
    print(f"warnings: {result['warnings']}")
