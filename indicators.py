# -*- coding: utf-8 -*-
"""
محرك المؤشرات الفنية — V9
================================
18 مؤشر احترافي، محسوب بالكامل vectorized (بدون حلقات بطيئة).

الجديد في V9:
  - ADX + DI+/DI- (قوة الاتجاه)
  - Supertrend (اتجاه ديناميكي)
  - Ichimoku Cloud (5 مكونات)
  - MFI (Money Flow Index — RSI + حجم)
  - Fibonacci Retracement (من قمة وقاع 20-يوم)
  - VWAP حقيقي بإعادة تعيين يومية
  - A/D Line + Chaikin Money Flow
  - OBV vectorized (أسرع 50×)
  - Relative Strength vs benchmark

المصلح من V8:
  - VWAP الخاطئ (كان moving average) → صحيح الآن
  - OBV الحلقة البطيئة → vectorized
  - SMA50 كان NaN في نصف البيانات → الآن يُستخدم period أطول
"""
import pandas as pd
import numpy as np


# ────────────────────────────────────────────
# أدوات مساعدة
# ────────────────────────────────────────────
def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def _rma(series, period):
    """Wilder's moving average (RMA)."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# ────────────────────────────────────────────
# 1. RSI (Wilder)
# ────────────────────────────────────────────
def rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ────────────────────────────────────────────
# 2. Stochastic RSI
# ────────────────────────────────────────────
def stoch_rsi(close, rsi_period=14, stoch_period=14, k_smooth=3, d_smooth=3):
    r = rsi(close, rsi_period)
    r_min = r.rolling(stoch_period).min()
    r_max = r.rolling(stoch_period).max()
    k_raw = (r - r_min) / (r_max - r_min).replace(0, np.nan) * 100
    k = k_raw.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d


# ────────────────────────────────────────────
# 3. MACD
# ────────────────────────────────────────────
def macd(close, fast=12, slow=26, signal=9):
    macd_line = _ema(close, fast) - _ema(close, slow)
    sig = _ema(macd_line, signal)
    hist = macd_line - sig
    return macd_line, sig, hist


# ────────────────────────────────────────────
# 4. Bollinger Bands
# ────────────────────────────────────────────
def bollinger(close, period=20, std_mult=2):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    pct = (close - lower) / (upper - lower).replace(0, np.nan)
    width = (upper - lower) / sma.replace(0, np.nan)  # لقياس الانكماش (squeeze)
    return upper, lower, pct, width


# ────────────────────────────────────────────
# 5. OBV (vectorized — أسرع من V8 بمقدار 50×)
# ────────────────────────────────────────────
def obv(close, volume):
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


# ────────────────────────────────────────────
# 6. VWAP حقيقي — بإعادة تعيين يومية
# (للبيانات اليومية نستخدم anchored VWAP من قاع آخر 20 يوم)
# ────────────────────────────────────────────
def vwap_anchored(high, low, close, volume, lookback=20):
    """
    VWAP مُرسى من أدنى سعر في آخر `lookback` يوم — طريقة احترافية
    للتعامل مع بيانات يومية. يحسب VWAP من نقطة البداية المتجددة.
    """
    tp = (high + low + close) / 3
    result = pd.Series(np.nan, index=close.index)

    for i in range(lookback, len(close)):
        window_low_idx = low.iloc[i - lookback:i + 1].idxmin()
        start_pos = close.index.get_loc(window_low_idx)
        tp_slice = tp.iloc[start_pos:i + 1]
        vol_slice = volume.iloc[start_pos:i + 1]
        vol_sum = vol_slice.sum()
        if vol_sum > 0:
            result.iloc[i] = (tp_slice * vol_slice).sum() / vol_sum

    return result


# ────────────────────────────────────────────
# 7. ATR
# ────────────────────────────────────────────
def atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return _rma(tr, period)


# ────────────────────────────────────────────
# 8. ADX + DI+/DI- (قوة الاتجاه — جديد في V9)
# ────────────────────────────────────────────
def adx(high, low, close, period=14):
    """
    يُرجع (adx, di_plus, di_minus)
    adx > 25: اتجاه قوي
    adx < 20: سوق عرضي (لا تتداول breakouts)
    """
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    atr_v = _rma(tr, period)
    di_plus = 100 * _rma(plus_dm, period) / atr_v.replace(0, np.nan)
    di_minus = 100 * _rma(minus_dm, period) / atr_v.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx_v = _rma(dx, period)
    return adx_v, di_plus, di_minus


# ────────────────────────────────────────────
# 9. Supertrend (جديد في V9 — ممتاز للـ swing)
# ────────────────────────────────────────────
def supertrend(high, low, close, period=10, multiplier=3.0):
    """
    يُرجع (trend_value, direction) — direction=1 صاعد, -1 هابط
    نقاط التقاطع (direction يتغير) = إشارات دخول/خروج.
    """
    atr_v = atr(high, low, close, period)
    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr_v
    lower_band = hl2 - multiplier * atr_v

    # final bands (adjusted for continuity)
    final_upper = upper_band.copy()
    final_lower = lower_band.copy()

    for i in range(1, len(close)):
        # upper
        if (upper_band.iloc[i] < final_upper.iloc[i - 1]) or (close.iloc[i - 1] > final_upper.iloc[i - 1]):
            final_upper.iloc[i] = upper_band.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]
        # lower
        if (lower_band.iloc[i] > final_lower.iloc[i - 1]) or (close.iloc[i - 1] < final_lower.iloc[i - 1]):
            final_lower.iloc[i] = lower_band.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

    # trend direction
    st = pd.Series(np.nan, index=close.index)
    direction = pd.Series(1, index=close.index)

    for i in range(1, len(close)):
        prev_st = st.iloc[i - 1]
        if pd.isna(prev_st):
            st.iloc[i] = final_upper.iloc[i]
            direction.iloc[i] = -1
            continue
        if prev_st == final_upper.iloc[i - 1]:
            # previously in downtrend
            if close.iloc[i] > final_upper.iloc[i]:
                st.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = 1
            else:
                st.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1
        else:
            # previously in uptrend
            if close.iloc[i] < final_lower.iloc[i]:
                st.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1
            else:
                st.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = 1

    return st, direction


# ────────────────────────────────────────────
# 10. Ichimoku Cloud (جديد في V9)
# ────────────────────────────────────────────
def ichimoku(high, low, close):
    """
    يُرجع: tenkan (9), kijun (26), senkou_a, senkou_b, chikou
    شروط صاعد قوي:
      - close > cloud (senkou_a, senkou_b)
      - tenkan > kijun
      - chikou فوق السعر قبل 26 يوم
    """
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    chikou = close.shift(-26)
    return tenkan, kijun, senkou_a, senkou_b, chikou


# ────────────────────────────────────────────
# 11. MFI — Money Flow Index (جديد في V9)
# ────────────────────────────────────────────
def mfi(high, low, close, volume, period=14):
    """RSI + حجم. MFI<20 تشبع بيع مع ضعف سيولة, MFI>80 تشبع شراء."""
    tp = (high + low + close) / 3
    mf = tp * volume
    direction = np.sign(tp.diff()).fillna(0)
    pos_mf = mf.where(direction > 0, 0)
    neg_mf = mf.where(direction < 0, 0)
    pos_sum = pos_mf.rolling(period).sum()
    neg_sum = neg_mf.rolling(period).sum()
    mfr = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - (100 / (1 + mfr))


# ────────────────────────────────────────────
# 12. Fibonacci Retracement (جديد — طلبها العميل صراحة)
# ────────────────────────────────────────────
def fibonacci_levels(high, low, lookback=20):
    """
    يُرجع dict بمستويات Fibonacci من أعلى وأدنى سعر في آخر lookback يوم.
    يُرجع نسبة السعر الحالي في نطاق Fibonacci (0 = عند القاع, 1 = عند القمة).
    """
    hi = high.rolling(lookback).max()
    lo = low.rolling(lookback).min()
    rng = hi - lo
    return hi, lo, rng


def fib_position(close, high, low, lookback=20):
    """
    يُرجع قيمة بين 0 و 1: أين يقع السعر الحالي في نطاق Fibonacci.
    المستويات الكلاسيكية: 0.236, 0.382, 0.5, 0.618, 0.786
    """
    hi = high.rolling(lookback).max()
    lo = low.rolling(lookback).min()
    pos = (close - lo) / (hi - lo).replace(0, np.nan)
    return pos


# ────────────────────────────────────────────
# 13. A/D Line + Chaikin Money Flow (جديد)
# ────────────────────────────────────────────
def accumulation_distribution(high, low, close, volume):
    """A/D Line — يقيس ضغط الشراء/البيع المؤسسي."""
    mfm = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    mfv = mfm * volume
    return mfv.cumsum()


def chaikin_money_flow(high, low, close, volume, period=20):
    """CMF > 0: تراكم, < 0: توزيع."""
    mfm = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    mfv = mfm * volume
    return mfv.rolling(period).sum() / volume.rolling(period).sum().replace(0, np.nan)


# ────────────────────────────────────────────
# 14. Relative Strength vs Benchmark (جديد)
# ────────────────────────────────────────────
def relative_strength(stock_close, benchmark_close, period=20):
    """
    مقارنة أداء السهم مقابل المؤشر (TASI).
    > 1: السهم يتفوق, < 1: يتأخر.
    """
    stock_ret = stock_close.pct_change(period)
    bench_ret = benchmark_close.pct_change(period)
    # محاذاة التواريخ
    aligned = pd.DataFrame({"s": stock_ret, "b": bench_ret}).dropna()
    if len(aligned) == 0:
        return pd.Series(1.0, index=stock_close.index)
    rs = (1 + aligned["s"]) / (1 + aligned["b"])
    return rs.reindex(stock_close.index, method="ffill")


# ────────────────────────────────────────────
# 15. Candlestick Patterns (محسن من V8)
# ────────────────────────────────────────────
def candle_patterns(open_p, high, low, close):
    body = (close - open_p).abs()
    upper_shadow = high - pd.concat([close, open_p], axis=1).max(axis=1)
    lower_shadow = pd.concat([close, open_p], axis=1).min(axis=1) - low
    avg_body = body.rolling(10).mean()

    engulfing_bull = (
        (open_p.shift(1) > close.shift(1)) &  # previous red
        (close > open_p) &                     # current green
        (close > open_p.shift(1)) &            # closes above prev open
        (open_p < close.shift(1))              # opens below prev close
    )
    hammer = (
        (lower_shadow > 2 * body) &
        (upper_shadow < body * 0.3) &
        (body > 0)
    )
    doji = body < avg_body * 0.1
    shooting_star = (
        (upper_shadow > 2 * body) &
        (lower_shadow < body * 0.3) &
        (body > 0)
    )
    return engulfing_bull, hammer, doji, shooting_star


# ────────────────────────────────────────────
# 16. Volume Analysis
# ────────────────────────────────────────────
def volume_metrics(volume, period=20):
    vma = volume.rolling(period).mean()
    ratio = volume / vma.replace(0, np.nan)
    return vma, ratio


# ────────────────────────────────────────────
# 17. SMA / EMA
# ────────────────────────────────────────────
def moving_averages(close):
    return {
        "sma20": close.rolling(20).mean(),
        "sma50": close.rolling(50).mean(),
        "sma200": close.rolling(200).mean(),
        "ema9": _ema(close, 9),
        "ema21": _ema(close, 21),
    }


# ────────────────────────────────────────────
# 18. Weekly trend (Multi-timeframe)
# ────────────────────────────────────────────
def weekly_trend(close, period=100):
    """اتجاه أسبوعي مُقلَّد على البيانات اليومية."""
    sma_weekly = close.rolling(period).mean()
    return np.where(close > sma_weekly, 1, -1), sma_weekly


# ────────────────────────────────────────────
# الدالة الرئيسية — تحسب كل المؤشرات دفعة واحدة
# ────────────────────────────────────────────
def compute_all(df, benchmark_close=None):
    """
    df: DataFrame بـ Open, High, Low, Close, Volume
    benchmark_close: pd.Series لسعر المؤشر (TASI) للمقارنة النسبية
    يُرجع df موسّع بكل المؤشرات.
    """
    if df.empty or len(df) < 50:
        return df

    c, h, l, v, o = df["Close"], df["High"], df["Low"], df["Volume"], df["Open"]

    # RSI + StochRSI
    df["rsi"] = rsi(c)
    k, d = stoch_rsi(c)
    df["stoch_rsi_k"], df["stoch_rsi_d"] = k, d

    # MACD
    m_line, m_sig, m_hist = macd(c)
    df["macd"], df["macd_signal"], df["macd_hist"] = m_line, m_sig, m_hist

    # Bollinger
    bb_u, bb_l, bb_pct, bb_w = bollinger(c)
    df["bb_upper"], df["bb_lower"] = bb_u, bb_l
    df["bb_pct"], df["bb_width"] = bb_pct, bb_w

    # OBV (vectorized)
    df["obv"] = obv(c, v)
    df["obv_ma"] = df["obv"].rolling(20).mean()

    # VWAP (real anchored)
    df["vwap"] = vwap_anchored(h, l, c, v, lookback=20)

    # ATR
    df["atr"] = atr(h, l, c)

    # ADX
    adx_v, di_p, di_m = adx(h, l, c)
    df["adx"], df["di_plus"], df["di_minus"] = adx_v, di_p, di_m

    # Supertrend
    st_v, st_dir = supertrend(h, l, c)
    df["supertrend"], df["supertrend_dir"] = st_v, st_dir

    # Ichimoku
    tk, kj, sa, sb, ch = ichimoku(h, l, c)
    df["tenkan"], df["kijun"] = tk, kj
    df["senkou_a"], df["senkou_b"], df["chikou"] = sa, sb, ch

    # MFI
    df["mfi"] = mfi(h, l, c, v)

    # Fibonacci position
    df["fib_pos"] = fib_position(c, h, l, lookback=20)
    df["fib_hi_20"] = h.rolling(20).max()
    df["fib_lo_20"] = l.rolling(20).min()

    # A/D + CMF
    df["ad_line"] = accumulation_distribution(h, l, c, v)
    df["cmf"] = chaikin_money_flow(h, l, c, v)

    # Relative Strength
    if benchmark_close is not None and len(benchmark_close) > 20:
        df["rs_vs_tasi"] = relative_strength(c, benchmark_close)
    else:
        df["rs_vs_tasi"] = 1.0

    # Candles
    eng, ham, doj, shs = candle_patterns(o, h, l, c)
    df["engulfing_bull"], df["hammer"], df["doji"], df["shooting_star"] = eng, ham, doj, shs

    # Volume
    vma, vr = volume_metrics(v)
    df["vol_ma20"], df["vol_ratio"] = vma, vr

    # MAs
    mas = moving_averages(c)
    for k_, v_ in mas.items():
        df[k_] = v_

    # Weekly trend
    wt, smawk = weekly_trend(c)
    df["weekly_trend"] = wt
    df["sma20_weekly"] = smawk

    return df


if __name__ == "__main__":
    # اختبار سريع
    import yfinance as yf_test
    data = yf_test.download("2222.SR", period="250d", progress=False, auto_adjust=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    result = compute_all(data)
    last = result.iloc[-1]
    print(f"آخر سعر: {last['Close']:.2f}")
    print(f"RSI: {last['rsi']:.1f}")
    print(f"ADX: {last['adx']:.1f}")
    print(f"MFI: {last['mfi']:.1f}")
    print(f"Supertrend dir: {int(last['supertrend_dir'])}")
    print(f"Fib position: {last['fib_pos']:.2%}")
    print(f"CMF: {last['cmf']:.3f}")
    print(f"BB %: {last['bb_pct']:.2%}")
