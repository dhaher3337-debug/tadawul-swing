# -*- coding: utf-8 -*-
"""
محرك الارتباطات وتدفق السيولة — V9
========================================
يُجيب على أسئلة المشروع الأساسية:
  1. أي سهم يرتفع → أي سهم يرتفع معه عادة؟ (correlation matrix)
  2. أي قطاع يقود السوق اليوم؟ (sector rotation)
  3. أين تتدفق السيولة؟ (net money flow per sector)
  4. من قائد القطاع ومن المتأخر؟ (leader/laggard detection)
  5. تبادل الأدوار بين القطاعات (rotation trend)

المخرجات تُغذّي Claude Opus بمعلومات استراتيجية قوية.
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


# ────────────────────────────────────────────
# قادة القطاعات (أكبر أسهم بالسيولة والوزن)
# ────────────────────────────────────────────
SECTOR_LEADERS = {
    "بنوك": "1120",        # الراجحي (قائد السوق)
    "بتروكيماويات": "2010", # سابك
    "طاقة": "2222",        # أرامكو
    "اتصالات": "7010",     # STC
    "تأمين": "8010",       # التعاونية
    "تجزئة": "4190",       # جرير
    "أسمنت": "3030",       # اليمامة
    "عقار": "4300",        # دار الأركان
    "زراعة وأغذية": "2280", # المراعي
    "مرافق": "5110",       # الكهرباء
    "رعاية صحية": "4004",  # دله الصحية
    "نقل": "4040",         # سابتكو
    "خدمات مالية": "1111", # STC Pay / البلاد المالية
    "صناعية": "1210",      # BCI
    "تقنية": "7200",       # موبايلي لحلول
    "إعلام": "4210",       # MBC / الرياض
    "فنادق": "1810",       # سياحة وطيران
}


def compute_correlation_matrix(stocks_data, period_days=30):
    """
    يحسب مصفوفة ارتباط أسعار الإغلاق بين الأسهم في آخر `period_days` يوم.

    Args:
        stocks_data: dict {ticker: DataFrame}  (الأعمدة Open,High,Low,Close,Volume)
        period_days: مدة حساب الارتباط
    Returns:
        pd.DataFrame: correlation matrix
    """
    closes = {}
    for t, df in stocks_data.items():
        if df is None or df.empty or len(df) < period_days:
            continue
        closes[t] = df["Close"].pct_change().tail(period_days)

    if not closes:
        return pd.DataFrame()

    combined = pd.DataFrame(closes).dropna(how="all")
    return combined.corr()


def find_highly_correlated_pairs(corr_matrix, threshold=0.75, limit=20):
    """
    يُرجع أعلى الأزواج ارتباطاً (للاستفادة: إذا تحرك أحدهما، الآخر غالباً يتبع).

    Returns:
        list of (ticker_a, ticker_b, correlation)
    """
    if corr_matrix.empty:
        return []

    pairs = []
    tickers = corr_matrix.index.tolist()
    for i, a in enumerate(tickers):
        for b in tickers[i + 1:]:
            c = corr_matrix.loc[a, b]
            if pd.notna(c) and c >= threshold:
                pairs.append((a, b, round(float(c), 3)))

    pairs.sort(key=lambda x: -x[2])
    return pairs[:limit]


def find_divergent_pairs(corr_matrix, stocks_data, threshold=0.7):
    """
    الأزواج العادة مرتبطة بقوة لكنها تباعدت اليوم — فرصة تداول محتملة:
    A ارتفع اليوم لكن B لم يرتفع رغم ارتباطهما التاريخي الكبير.

    Returns:
        list of {"leader": A, "laggard": B, "corr": float, "leader_chg": %, "laggard_chg": %}
    """
    if corr_matrix.empty:
        return []

    divergent = []
    for a in corr_matrix.index:
        for b in corr_matrix.columns:
            if a == b:
                continue
            c = corr_matrix.loc[a, b]
            if pd.isna(c) or c < threshold:
                continue
            # تغير اليوم لكل منهما
            da = stocks_data.get(a)
            db = stocks_data.get(b)
            if da is None or db is None or len(da) < 2 or len(db) < 2:
                continue
            ca = float(da["Close"].iloc[-1])
            pa = float(da["Close"].iloc[-2])
            cb = float(db["Close"].iloc[-1])
            pb = float(db["Close"].iloc[-2])
            chg_a = (ca - pa) / pa * 100
            chg_b = (cb - pb) / pb * 100
            # A قائد صاعد, B متأخر
            if chg_a > 1.5 and chg_b < 0.5 and (chg_a - chg_b) > 1.5:
                divergent.append({
                    "leader": a, "laggard": b,
                    "corr": round(float(c), 2),
                    "leader_chg": round(chg_a, 2),
                    "laggard_chg": round(chg_b, 2),
                    "spread": round(chg_a - chg_b, 2),
                })

    divergent.sort(key=lambda x: -x["spread"])
    return divergent[:15]


# ────────────────────────────────────────────
# دوران القطاعات (Sector Rotation)
# ────────────────────────────────────────────
def compute_sector_flow(stocks_data, ticker_sector_map, lookback_days=5):
    """
    يقيس تدفق السيولة لكل قطاع خلال آخر `lookback_days`:
      money_flow = Σ (volume × price × direction)
    حيث direction = +1 إذا الإغلاق > فتح, -1 إذا العكس.

    Returns:
        dict: {sector: {
            "net_flow_5d": float,        # صافي التدفق 5 أيام
            "avg_change_5d": float,      # متوسط التغير %
            "momentum_trend": "accelerating"|"decelerating"|"stable",
            "leader": str,               # قائد القطاع هذا الأسبوع
            "laggard": str,              # متأخر القطاع
        }}
    """
    sectors = {}

    for ticker, df in stocks_data.items():
        sec = ticker_sector_map.get(ticker.replace(".SR", ""), "أخرى")
        if df is None or df.empty or len(df) < lookback_days + 2:
            continue

        df_recent = df.tail(lookback_days)
        # money flow يومي
        direction = np.sign(df_recent["Close"] - df_recent["Open"])
        daily_flow = direction * df_recent["Volume"] * df_recent["Close"]

        # تغير السهم خلال الفترة
        first_close = float(df_recent["Close"].iloc[0])
        last_close = float(df_recent["Close"].iloc[-1])
        stock_change = (last_close - first_close) / first_close * 100 if first_close else 0

        # momentum: أول نصف vs ثاني نصف من 5 أيام
        mid = lookback_days // 2
        first_half_flow = daily_flow.iloc[:mid].sum() if mid > 0 else 0
        second_half_flow = daily_flow.iloc[mid:].sum()

        if sec not in sectors:
            sectors[sec] = {
                "tickers": [],
                "net_flow_5d": 0.0,
                "first_half_flow": 0.0,
                "second_half_flow": 0.0,
                "stock_changes": [],
            }

        sectors[sec]["tickers"].append(ticker.replace(".SR", ""))
        sectors[sec]["net_flow_5d"] += float(daily_flow.sum())
        sectors[sec]["first_half_flow"] += float(first_half_flow)
        sectors[sec]["second_half_flow"] += float(second_half_flow)
        sectors[sec]["stock_changes"].append((ticker.replace(".SR", ""), stock_change))

    # معالجة
    result = {}
    for sec, info in sectors.items():
        changes = info["stock_changes"]
        if not changes:
            continue
        changes.sort(key=lambda x: -x[1])
        avg_chg = sum(c for _, c in changes) / len(changes)

        # ترند الزخم
        if info["first_half_flow"] == 0:
            trend = "stable"
        else:
            accel_ratio = info["second_half_flow"] / abs(info["first_half_flow"]) if info["first_half_flow"] != 0 else 1
            if accel_ratio > 1.5:
                trend = "تسارع قوي"
            elif accel_ratio > 1.1:
                trend = "تسارع"
            elif accel_ratio < 0.5:
                trend = "تباطؤ قوي"
            elif accel_ratio < 0.9:
                trend = "تباطؤ"
            else:
                trend = "مستقر"

        result[sec] = {
            "net_flow_5d": round(info["net_flow_5d"] / 1_000_000, 2),  # مليون ريال
            "avg_change_5d": round(avg_chg, 2),
            "momentum_trend": trend,
            "leader": changes[0][0] if changes else "",
            "leader_change": round(changes[0][1], 2) if changes else 0,
            "laggard": changes[-1][0] if len(changes) > 1 else "",
            "laggard_change": round(changes[-1][1], 2) if len(changes) > 1 else 0,
            "stock_count": len(changes),
        }

    return result


def detect_sector_rotation(current_flows, prev_flows=None):
    """
    يكتشف تبادل الأدوار: أي قطاع كان ضعيفاً وأصبح قوياً، والعكس.

    Args:
        current_flows: نتائج compute_sector_flow اليوم
        prev_flows: نتائج الأمس أو الأسبوع الماضي (اختياري)
    Returns:
        list of {"sector": str, "rotation": "دخول"|"خروج", "prev_change": float, "now_change": float}
    """
    if not prev_flows:
        return []

    rotations = []
    for sec, now in current_flows.items():
        prev = prev_flows.get(sec)
        if not prev:
            continue
        now_chg = now["avg_change_5d"]
        prev_chg = prev.get("avg_change_5d", 0)
        delta = now_chg - prev_chg

        if prev_chg < -1 and now_chg > 1 and delta > 2:
            rotations.append({
                "sector": sec,
                "rotation": "دخول سيولة ↗",
                "prev_change": prev_chg,
                "now_change": now_chg,
                "delta": round(delta, 2),
            })
        elif prev_chg > 1 and now_chg < -1 and delta < -2:
            rotations.append({
                "sector": sec,
                "rotation": "خروج سيولة ↘",
                "prev_change": prev_chg,
                "now_change": now_chg,
                "delta": round(delta, 2),
            })

    rotations.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return rotations


# ────────────────────────────────────────────
# Leader/Laggard analysis داخل القطاع
# ────────────────────────────────────────────
def leader_laggard_per_sector(stocks_data, ticker_sector_map, lookback=3):
    """
    لكل قطاع: يجد السهم القائد (ارتفع أولاً) والمتأخر (لم يتحرك بعد).
    فائدة: إذا قائد القطاع ارتفع بقوة لكن المتأخر لم يتحرك,
    المتأخر فرصة "catch-up trade".

    Returns:
        dict {sector: {"leader":..., "laggard":..., "catch_up_candidate": bool}}
    """
    by_sector = {}

    for ticker, df in stocks_data.items():
        if df is None or df.empty or len(df) < lookback + 2:
            continue
        code = ticker.replace(".SR", "")
        sec = ticker_sector_map.get(code, "أخرى")

        last = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-lookback - 1])
        chg = (last - prev) / prev * 100 if prev else 0

        by_sector.setdefault(sec, []).append((code, chg))

    result = {}
    for sec, stocks in by_sector.items():
        if len(stocks) < 2:
            continue
        stocks.sort(key=lambda x: -x[1])
        leader_code, leader_chg = stocks[0]
        laggard_code, laggard_chg = stocks[-1]

        # فرصة catch-up: القائد +3%+ والمتأخر < 0
        catch_up = leader_chg > 3.0 and laggard_chg < 0.0

        result[sec] = {
            "leader": leader_code,
            "leader_change": round(leader_chg, 2),
            "laggard": laggard_code,
            "laggard_change": round(laggard_chg, 2),
            "catch_up_opportunity": catch_up,
            "spread": round(leader_chg - laggard_chg, 2),
        }

    return result


# ────────────────────────────────────────────
# ملخص تنفيذي للـ AI
# ────────────────────────────────────────────
def build_intermarket_summary(stocks_data, ticker_sector_map, prev_sector_flows=None):
    """
    يجمع كل التحليلات في ملخص واحد جاهز لـ Claude Opus.
    """
    corr_mat = compute_correlation_matrix(stocks_data, period_days=30)

    summary = {
        "highly_correlated_pairs": find_highly_correlated_pairs(corr_mat, threshold=0.75, limit=10),
        "divergent_pairs": find_divergent_pairs(corr_mat, stocks_data, threshold=0.7),
        "sector_flows": compute_sector_flow(stocks_data, ticker_sector_map, lookback_days=5),
        "leader_laggard": leader_laggard_per_sector(stocks_data, ticker_sector_map, lookback=3),
    }

    summary["sector_rotation"] = detect_sector_rotation(
        summary["sector_flows"], prev_sector_flows
    )

    return summary
