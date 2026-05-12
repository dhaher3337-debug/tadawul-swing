# -*- coding: utf-8 -*-
"""
power_classifier.py — Tadawul Swing V9.2 (Real Integration)
==============================================================
يدمج منطق Gemini V301 Ultra Pine Script في scanner_v9.py.

📌 مهم: هذه النسخة معدّلة على البنية الفعلية لـ V9.2:
- يأخذ DataFrame مباشرة من yfinance/scanner_v9
- يُرجع dict مدمج مع candidate الموجود (لا يستبدله)
- متوافق مع schema الـ scanner الحالي (ticker, signals, score, ...)

📊 ما يضيفه لكل candidate:
- power_score: 0-100 (نقاط قوة الكسر)
- power_classification: ROCKET / STRONG / WEAK / CRASH / DUMP / NONE
- power_direction: UP / DOWN / NONE
- power_breakdown: dict (تفصيل النقاط)
- power_emoji: للعرض في reports

🔧 الاستخدام في scanner_v9.py:
    from power_classifier import enrich_candidate_with_power
    
    for candidate in candidates:
        df = stocks_data[candidate['ticker']]
        candidate = enrich_candidate_with_power(candidate, df)

المؤلف: Dhaher | الإصدار: 2.0 | مايو 2026
"""
import pandas as pd
import numpy as np
import logging
from typing import Optional

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 📋 الإعدادات (مطابقة لـ V301 Pine Script)
# ═══════════════════════════════════════════════════════════

PIVOT_LEFT = 5
PIVOT_RIGHT = 5

VOL_MULT_HIGH = 2.0
VOL_MULT_MED = 1.5
VOL_MULT_LOW = 1.0

RSI_LOW_UP = 55
RSI_HIGH_UP = 72
RSI_LOW_DN = 28
RSI_HIGH_DN = 45

ATR_MULT = 1.3
BODY_RATIO_STRONG = 0.7
BODY_RATIO_MED = 0.5

CLOSE_POS_STRONG = 0.75
CLOSE_POS_MED = 0.60

BB_SQUEEZE_RATIO = 0.8

SCORE_ROCKET = 80
SCORE_STRONG = 65
SCORE_MIN_DISPLAY = 50


# ═══════════════════════════════════════════════════════════
# 🔧 المؤشرات الفنية
# ═══════════════════════════════════════════════════════════

def _calculate_indicators(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """حساب المؤشرات. يدعم أعمدة بأي capitalization."""
    df = df.copy()
    
    # توحيد أسماء الأعمدة
    column_map = {}
    for col in df.columns:
        col_lower = col.lower() if isinstance(col, str) else col
        if col_lower in ['open', 'high', 'low', 'close', 'volume']:
            column_map[col] = col_lower
    df = df.rename(columns=column_map)
    
    required = ['open', 'high', 'low', 'close', 'volume']
    if not all(c in df.columns for c in required):
        return None
    
    # Volume
    df['vol_avg'] = df['volume'].rolling(20).mean()
    
    # EMAs
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    # RSI
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = (100 - (100 / (1 + rs))).fillna(50)
    
    # ATR
    h_l = df['high'] - df['low']
    h_c = (df['high'] - df['close'].shift(1)).abs()
    l_c = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([h_l, h_c, l_c], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    df['atr_avg'] = df['atr'].rolling(20).mean()
    
    # Bollinger Width
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_width'] = (4 * df['bb_std']) / df['bb_mid']
    df['bb_width_avg'] = df['bb_width'].rolling(20).mean()
    
    # Candle metrics
    df['c_range'] = df['high'] - df['low']
    df['c_body'] = (df['close'] - df['open']).abs()
    df['body_ratio'] = df['c_body'] / df['c_range'].replace(0, np.nan)
    df['close_pos_up'] = (df['close'] - df['low']) / df['c_range'].replace(0, np.nan)
    df['close_pos_dn'] = (df['high'] - df['close']) / df['c_range'].replace(0, np.nan)
    
    return df


# ═══════════════════════════════════════════════════════════
# 🎯 Pivot Detection
# ═══════════════════════════════════════════════════════════

def _find_recent_pivots(df: pd.DataFrame, lookback: int = 60):
    """إيجاد آخر swing high و swing low مؤكدين."""
    if len(df) < lookback + PIVOT_LEFT + PIVOT_RIGHT:
        return None, None
    
    highs = df['high'].values
    lows = df['low'].values
    n = len(df)
    
    swing_high = None
    swing_low = None
    
    start_idx = n - PIVOT_RIGHT - 1
    end_idx = max(PIVOT_LEFT, n - lookback)
    
    for i in range(start_idx, end_idx, -1):
        if swing_high is None:
            window = highs[i-PIVOT_LEFT:i+PIVOT_RIGHT+1]
            if highs[i] == window.max():
                swing_high = float(highs[i])
        
        if swing_low is None:
            window = lows[i-PIVOT_LEFT:i+PIVOT_RIGHT+1]
            if lows[i] == window.min():
                swing_low = float(lows[i])
        
        if swing_high is not None and swing_low is not None:
            break
    
    return swing_high, swing_low


# ═══════════════════════════════════════════════════════════
# 🧮 Scoring (7 فلاتر = 100 نقطة)
# ═══════════════════════════════════════════════════════════

def _sv(row, key, default=0.0):
    """قراءة آمنة."""
    try:
        v = row[key]
        if pd.isna(v):
            return default
        return float(v)
    except (KeyError, TypeError, ValueError):
        return default


def _score_bullish(row) -> tuple:
    """نقاط الكسر الصاعد."""
    score = 0
    bd = {}
    
    volume = _sv(row, 'volume')
    vol_avg = _sv(row, 'vol_avg', 1)
    close = _sv(row, 'close')
    open_ = _sv(row, 'open')
    ema50 = _sv(row, 'ema50')
    ema200 = _sv(row, 'ema200')
    rsi = _sv(row, 'rsi', 50)
    atr = _sv(row, 'atr')
    atr_avg = _sv(row, 'atr_avg', 1)
    body_ratio = _sv(row, 'body_ratio')
    close_pos = _sv(row, 'close_pos_up')
    bb_width = _sv(row, 'bb_width', 1)
    bb_width_avg = _sv(row, 'bb_width_avg', 1)
    
    # 1. Volume (25)
    ratio = volume / vol_avg if vol_avg > 0 else 0
    if ratio >= VOL_MULT_HIGH:
        bd['volume'] = 25
    elif ratio >= VOL_MULT_MED:
        bd['volume'] = 15
    elif ratio >= VOL_MULT_LOW:
        bd['volume'] = 8
    else:
        bd['volume'] = 0
    score += bd['volume']
    
    # 2. Trend (20)
    if ema50 > ema200 and close > ema50:
        bd['trend'] = 20
    elif close > ema50:
        bd['trend'] = 12
    elif close > ema200:
        bd['trend'] = 6
    else:
        bd['trend'] = 0
    score += bd['trend']
    
    # 3. Candle (15)
    is_bull = close > open_
    if body_ratio > BODY_RATIO_STRONG and is_bull:
        bd['candle'] = 15
    elif body_ratio > BODY_RATIO_MED and is_bull:
        bd['candle'] = 10
    elif is_bull:
        bd['candle'] = 5
    else:
        bd['candle'] = 0
    score += bd['candle']
    
    # 4. RSI (15)
    if RSI_LOW_UP <= rsi <= RSI_HIGH_UP:
        bd['rsi'] = 15
    elif RSI_HIGH_UP < rsi < 80:
        bd['rsi'] = 8
    elif 50 <= rsi < RSI_LOW_UP:
        bd['rsi'] = 10
    else:
        bd['rsi'] = 0
    score += bd['rsi']
    
    # 5. ATR (10)
    if atr_avg > 0:
        if atr > atr_avg * ATR_MULT:
            bd['atr'] = 10
        elif atr > atr_avg:
            bd['atr'] = 5
        else:
            bd['atr'] = 0
    else:
        bd['atr'] = 0
    score += bd['atr']
    
    # 6. Close Position (10)
    if close_pos > CLOSE_POS_STRONG:
        bd['close_pos'] = 10
    elif close_pos > CLOSE_POS_MED:
        bd['close_pos'] = 6
    else:
        bd['close_pos'] = 0
    score += bd['close_pos']
    
    # 7. Squeeze (5)
    if bb_width_avg > 0 and bb_width < bb_width_avg * BB_SQUEEZE_RATIO:
        bd['squeeze'] = 5
    else:
        bd['squeeze'] = 0
    score += bd['squeeze']
    
    return score, bd


def _score_bearish(row) -> tuple:
    """نقاط الكسر الهابط."""
    score = 0
    bd = {}
    
    volume = _sv(row, 'volume')
    vol_avg = _sv(row, 'vol_avg', 1)
    close = _sv(row, 'close')
    open_ = _sv(row, 'open')
    ema50 = _sv(row, 'ema50')
    ema200 = _sv(row, 'ema200')
    rsi = _sv(row, 'rsi', 50)
    atr = _sv(row, 'atr')
    atr_avg = _sv(row, 'atr_avg', 1)
    body_ratio = _sv(row, 'body_ratio')
    close_pos_dn = _sv(row, 'close_pos_dn')
    bb_width = _sv(row, 'bb_width', 1)
    bb_width_avg = _sv(row, 'bb_width_avg', 1)
    
    # 1. Volume (25)
    ratio = volume / vol_avg if vol_avg > 0 else 0
    if ratio >= VOL_MULT_HIGH:
        bd['volume'] = 25
    elif ratio >= VOL_MULT_MED:
        bd['volume'] = 15
    elif ratio >= VOL_MULT_LOW:
        bd['volume'] = 8
    else:
        bd['volume'] = 0
    score += bd['volume']
    
    # 2. Trend (20)
    if ema50 < ema200 and close < ema50:
        bd['trend'] = 20
    elif close < ema50:
        bd['trend'] = 12
    elif close < ema200:
        bd['trend'] = 6
    else:
        bd['trend'] = 0
    score += bd['trend']
    
    # 3. Candle (15)
    is_bear = close < open_
    if body_ratio > BODY_RATIO_STRONG and is_bear:
        bd['candle'] = 15
    elif body_ratio > BODY_RATIO_MED and is_bear:
        bd['candle'] = 10
    elif is_bear:
        bd['candle'] = 5
    else:
        bd['candle'] = 0
    score += bd['candle']
    
    # 4. RSI (15)
    if RSI_LOW_DN <= rsi <= RSI_HIGH_DN:
        bd['rsi'] = 15
    elif 20 < rsi < RSI_LOW_DN:
        bd['rsi'] = 8
    elif RSI_HIGH_DN < rsi <= 50:
        bd['rsi'] = 10
    else:
        bd['rsi'] = 0
    score += bd['rsi']
    
    # 5. ATR (10)
    if atr_avg > 0:
        if atr > atr_avg * ATR_MULT:
            bd['atr'] = 10
        elif atr > atr_avg:
            bd['atr'] = 5
        else:
            bd['atr'] = 0
    else:
        bd['atr'] = 0
    score += bd['atr']
    
    # 6. Close Position Down (10)
    if close_pos_dn > CLOSE_POS_STRONG:
        bd['close_pos'] = 10
    elif close_pos_dn > CLOSE_POS_MED:
        bd['close_pos'] = 6
    else:
        bd['close_pos'] = 0
    score += bd['close_pos']
    
    # 7. Squeeze (5)
    if bb_width_avg > 0 and bb_width < bb_width_avg * BB_SQUEEZE_RATIO:
        bd['squeeze'] = 5
    else:
        bd['squeeze'] = 0
    score += bd['squeeze']
    
    return score, bd


def _classify(score: int, direction: str) -> tuple:
    """تصنيف الإشارة."""
    if direction == "UP":
        if score >= SCORE_ROCKET:
            return "ROCKET", "🚀🚀🚀"
        elif score >= SCORE_STRONG:
            return "STRONG", "🚀🚀"
        elif score >= SCORE_MIN_DISPLAY:
            return "WEAK", "🚀"
        return "NONE", ""
    else:
        if score >= SCORE_ROCKET:
            return "CRASH", "💀💀💀"
        elif score >= SCORE_STRONG:
            return "DUMP", "🔻🔻"
        elif score >= SCORE_MIN_DISPLAY:
            return "WEAK_DN", "🔻"
        return "NONE", ""


def _empty_result() -> dict:
    return {
        'power_score': 0,
        'power_classification': 'NONE',
        'power_direction': 'NONE',
        'power_breakdown': {},
        'power_emoji': '',
        'power_breakout_level': 0.0,
        'power_targets': {},
    }


# ═══════════════════════════════════════════════════════════
# 🚀 المحلل الرئيسي
# ═══════════════════════════════════════════════════════════

def analyze_power(df: pd.DataFrame, ticker: str = "?") -> dict:
    """
    تحليل قوة الكسر لسهم واحد.
    
    Args:
        df: DataFrame مع OHLCV
        ticker: للتوثيق فقط
    
    Returns:
        dict بحقول power_*
    
    📌 منطق الكشف (V2 - مُحسّن):
    - نبحث عن "كسر حديث" خلال آخر 5 شموع (وليس فقط اليوم)
    - السبب: الكسر قد يكون حدث قبل أيام والسهم لا يزال فوق المستوى
    - نُسجّل الإشارة فقط إذا السعر الحالي ≥ السعر يوم الكسر (لم يفشل بعد)
    """
    if df is None or len(df) < 220:
        return _empty_result()
    
    df_ind = _calculate_indicators(df)
    if df_ind is None:
        return _empty_result()
    
    swing_high, swing_low = _find_recent_pivots(df_ind, lookback=60)
    if swing_high is None or swing_low is None:
        return _empty_result()
    
    if len(df_ind) < 6:  # نحتاج 5 شموع للبحث عن الكسر
        return _empty_result()
    
    last = df_ind.iloc[-1]
    last_close = _sv(last, 'close')
    
    if last_close == 0:
        return _empty_result()
    
    # حساب الأهداف
    range_val = swing_high - swing_low
    targets = {}
    if range_val > 0:
        targets = {
            'T1_up': round(swing_low + range_val * 1.618, 2),
            'T2_up': round(swing_low + range_val * 2.618, 2),
            'T3_up': round(swing_low + range_val * 3.618, 2),
            'T1_dn': round(swing_high - range_val * 1.618, 2),
            'T2_dn': round(swing_high - range_val * 2.618, 2),
            'T3_dn': round(swing_high - range_val * 3.618, 2),
        }
    
    # 🔍 البحث عن كسر حديث خلال آخر 20 شمعة
    # نبحث من الأحدث للأقدم - أول كسر نجده هو الذي نُسجّله
    # السبب: في الترندات الصاعدة، الكسر قد يكون قبل أسبوعين والسهم لا يزال فوق
    BREAKOUT_LOOKBACK = 20
    break_up_idx = None
    break_dn_idx = None
    
    for i in range(1, BREAKOUT_LOOKBACK + 1):
        if -i - 1 < -len(df_ind):
            break
        
        candle = df_ind.iloc[-i]
        prev_candle = df_ind.iloc[-i - 1]
        
        candle_close = _sv(candle, 'close')
        prev_candle_close = _sv(prev_candle, 'close')
        
        # كسر صاعد: قفز فوق swing_high
        if break_up_idx is None and prev_candle_close <= swing_high and candle_close > swing_high:
            # شرط إضافي: السعر الحالي لم ينهار تحت swing_high
            if last_close > swing_high * 0.98:  # نسمح بـ 2% تحت كـ pullback
                break_up_idx = -i
        
        # كسر هابط: انكسر تحت swing_low
        if break_dn_idx is None and prev_candle_close >= swing_low and candle_close < swing_low:
            if last_close < swing_low * 1.02:  # نسمح بـ 2% فوق كـ pullback
                break_dn_idx = -i
    
    # 🎯 إذا وُجد كسر صاعد (أحدث = أولوية)
    if break_up_idx is not None:
        # 📌 مهم: Score يُحسب على آخر شمعة (الوضع الحالي)
        # وليس على شمعة الكسر، لأننا نريد قياس قوة الزخم الحالي
        # كسر قديم + زخم قوي اليوم = إشارة قوية للاستمرار
        score, bd = _score_bullish(last)
        classification, emoji = _classify(score, "UP")
        
        # عدد الأيام منذ الكسر
        days_since = abs(break_up_idx)
        bd['days_since_breakout'] = days_since
        
        return {
            'power_score': int(score),
            'power_classification': classification,
            'power_direction': 'UP',
            'power_breakdown': bd,
            'power_emoji': emoji,
            'power_breakout_level': round(swing_high, 2),
            'power_targets': targets,
            'power_days_since': days_since,
        }
    
    # 🎯 إذا وُجد كسر هابط
    elif break_dn_idx is not None:
        score, bd = _score_bearish(last)
        classification, emoji = _classify(score, "DOWN")
        
        days_since = abs(break_dn_idx)
        bd['days_since_breakout'] = days_since
        
        return {
            'power_score': int(score),
            'power_classification': classification,
            'power_direction': 'DOWN',
            'power_breakdown': bd,
            'power_emoji': emoji,
            'power_breakout_level': round(swing_low, 2),
            'power_targets': targets,
            'power_days_since': days_since,
        }
    
    return _empty_result()


# ═══════════════════════════════════════════════════════════
# 🎯 Entry Point للدمج مع scanner_v9
# ═══════════════════════════════════════════════════════════

def enrich_candidate_with_power(candidate: dict, df: pd.DataFrame) -> dict:
    """
    تأخذ candidate من scanner و DataFrame وتُضيف power fields.
    
    استخدمها في scanner_v9.py مباشرة قبل save_json(F_CANDIDATES).
    
    Args:
        candidate: dict من scanner
        df: DataFrame من yfinance
    
    Returns:
        candidate المُحدَّث
    """
    ticker = candidate.get('ticker', '?')
    
    try:
        power_data = analyze_power(df, ticker)
        candidate.update(power_data)
        
        # 🔥 إضافة power_breakout كـ signal فني إذا كان قوياً
        # هذا يدمجه في نظام signals الموجود ويُحسب في len(signals)
        if power_data['power_classification'] in ('ROCKET', 'STRONG'):
            signals = candidate.get('signals', [])
            if 'power_breakout' not in signals:
                signals.append('power_breakout')
                candidate['signals'] = signals
        
        elif power_data['power_classification'] in ('CRASH', 'DUMP'):
            signals = candidate.get('signals', [])
            if 'power_breakdown' not in signals:
                signals.append('power_breakdown')
                candidate['signals'] = signals
        
    except Exception as e:
        log.warning(f"فشل power analysis لـ {ticker}: {e}")
        candidate.update(_empty_result())
    
    return candidate


# ═══════════════════════════════════════════════════════════
# 📊 ملخص لتقارير HTML
# ═══════════════════════════════════════════════════════════

def summarize_power_signals(candidates: list) -> dict:
    """يلخص إشارات Power لتقرير build_reports_v9."""
    rockets = [c for c in candidates if c.get('power_classification') == 'ROCKET']
    strongs = [c for c in candidates if c.get('power_classification') == 'STRONG']
    crashes = [c for c in candidates if c.get('power_classification') == 'CRASH']
    dumps = [c for c in candidates if c.get('power_classification') == 'DUMP']
    
    all_breakouts = rockets + strongs + crashes + dumps
    avg_score = (
        sum(c.get('power_score', 0) for c in all_breakouts) / len(all_breakouts)
        if all_breakouts else 0
    )
    
    return {
        'rockets': rockets,
        'strongs': strongs,
        'crashes': crashes,
        'dumps': dumps,
        'total_breakouts': len(all_breakouts),
        'avg_score': round(avg_score, 1),
    }


# ═══════════════════════════════════════════════════════════
# 🧪 اختبار
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("Power Classifier V2 — اختبار")
    print("=" * 60)
    
    try:
        import yfinance as yf
        
        test_symbols = ["2222.SR", "1120.SR", "2010.SR"]
        for sym in test_symbols:
            print(f"\n🔍 تحليل {sym}...")
            df = yf.download(sym, period="1y", interval="1d", progress=False)
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            if not df.empty:
                result = analyze_power(df, sym)
                if result['power_direction'] != 'NONE':
                    print(f"  {result['power_emoji']} {result['power_classification']} "
                          f"(Score: {result['power_score']}/100)")
                    print(f"  الاتجاه: {result['power_direction']}")
                    print(f"  مستوى الكسر: {result['power_breakout_level']}")
                    print(f"  التفصيل: {result['power_breakdown']}")
                else:
                    print(f"  لا يوجد كسر حالياً")
            else:
                print(f"  ⚠️ فشل تحميل البيانات")
    except ImportError:
        print("⚠️ ثبّت yfinance: pip install yfinance")
    except Exception as e:
        print(f"⚠️ خطأ: {e}")
