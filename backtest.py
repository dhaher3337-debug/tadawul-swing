# -*- coding: utf-8 -*-
"""
محرك Backtest — V9
==========================
يُشغّل النظام على 60-90 يوم ماضية لقياس الأداء الحقيقي.
يقيس:
  - win rate (نسبة النجاح الحقيقية)
  - متوسط الربح/الخسارة
  - Sharpe ratio
  - Max drawdown
  - Expected value per trade

الاستخدام:
  python backtest.py --days 60 --top 10
"""
import argparse
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

from data_sources import fetch_ohlcv_batch, fetch_ohlcv
from indicators import compute_all
from ml_engine import realistic_hit_label, extract_features_from_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def run_backtest(tickers, days=60, top_n=10, hit_target=1.5, hit_stop=-2.0, hold_days=3):
    """
    يُشغّل backtest بسيط:
      - لكل يوم في آخر `days` يوم:
        1. يحسب المؤشرات بالبيانات المتاحة حتى ذلك اليوم
        2. يرتب الأسهم بـ score
        3. يأخذ أفضل top_n
        4. يقيس أداءهم في الـ hold_days التالية (بوقف وهدف)
    """
    # جلب بيانات مرة واحدة لكل الفترة + buffer
    print(f"📡 جلب {len(tickers)} سهم لفترة {days+200} يوم...")
    stocks_data = fetch_ohlcv_batch(tickers, period_days=days + 200)
    print(f"✓ تم جلب {len(stocks_data)} سهم")

    # جلب TASI للـ relative strength
    tasi_df = fetch_ohlcv("^TASI.SR", period_days=days + 200)
    tasi_close = tasi_df["Close"] if not tasi_df.empty else None

    # حساب المؤشرات لكل سهم مرة واحدة
    print("🔧 حساب المؤشرات...")
    computed = {}
    for t, df in stocks_data.items():
        if df.empty or len(df) < 150:
            continue
        try:
            df_c = compute_all(df.copy(), benchmark_close=tasi_close)
            computed[t] = df_c
        except Exception as e:
            log.debug(f"{t}: {e}")

    print(f"✓ {len(computed)} سهم بمؤشرات مكتملة")

    # نموذج scoring مبسط للـ backtest (بدون ML، بدون AI)
    from scanner_v9 import score_stock, DEFAULT_WEIGHTS, get_oil_weight
    weights = DEFAULT_WEIGHTS.copy()

    all_trades = []
    daily_stats = []

    # نبدأ من أقدم تاريخ نملك فيه 100 شمعة قبله
    master_idx = sorted(set().union(*[df.index for df in computed.values()]))
    if len(master_idx) < days + 10:
        print("❌ بيانات غير كافية للـ backtest")
        return

    # نستخدم آخر `days` تواريخ للاختبار
    test_dates = master_idx[-days - hold_days - 1:-hold_days - 1]

    for test_date in test_dates:
        candidates = []
        for t, df_c in computed.items():
            if test_date not in df_c.index:
                continue
            pos = df_c.index.get_loc(test_date)
            if pos < 100:
                continue

            last = df_c.iloc[pos].to_dict()
            prev = df_c.iloc[pos - 1].to_dict()
            close = float(last.get("Close", 0))
            if close < 5:
                continue

            score, reasons, signals = score_stock(
                last, prev, weights, oil_chg_pct=0,
                code=t.replace(".SR", ""), ml_prob=None,
            )
            if score >= 3.0:
                candidates.append({
                    "ticker": t, "close": close, "score": score,
                    "signals": signals, "pos": pos, "df": df_c,
                })

        candidates.sort(key=lambda x: -x["score"])
        picks = candidates[:top_n]

        # قياس أداء كل pick في الـ hold_days التالية
        day_hits = 0
        day_pnl = []
        for p in picks:
            future = p["df"].iloc[p["pos"] + 1:p["pos"] + 1 + hold_days]
            if len(future) < 1:
                continue

            hit = realistic_hit_label(p["close"], future, hit_target, hit_stop, hold_days)
            if hit is None:
                continue

            # حساب الـ PnL: إذا ضُرب الوقف، الخسارة = stop_pct; إذا ضُرب الهدف، الربح = target_pct
            pnl = 0
            for _, bar in future.iterrows():
                low_pct = (float(bar["Low"]) - p["close"]) / p["close"] * 100
                high_pct = (float(bar["High"]) - p["close"]) / p["close"] * 100
                if low_pct <= hit_stop:
                    pnl = hit_stop
                    break
                if high_pct >= hit_target:
                    pnl = hit_target
                    break
            else:
                # لم يضرب وقف ولا هدف — نأخذ آخر إغلاق
                pnl = (float(future.iloc[-1]["Close"]) - p["close"]) / p["close"] * 100

            trade = {
                "date": test_date.strftime("%Y-%m-%d"),
                "ticker": p["ticker"].replace(".SR", ""),
                "entry": p["close"],
                "score": p["score"],
                "signals": p["signals"],
                "hit": hit,
                "pnl_pct": round(pnl, 2),
            }
            all_trades.append(trade)
            day_pnl.append(pnl)
            if hit:
                day_hits += 1

        if day_pnl:
            daily_stats.append({
                "date": test_date.strftime("%Y-%m-%d"),
                "picks": len(day_pnl),
                "hits": day_hits,
                "hit_rate": round(day_hits / len(day_pnl), 2),
                "avg_pnl": round(sum(day_pnl) / len(day_pnl), 2),
            })

    # ملخص
    if not all_trades:
        print("❌ لا توجد صفقات في الـ backtest")
        return

    df_trades = pd.DataFrame(all_trades)
    hit_rate = df_trades["hit"].mean() * 100
    avg_pnl = df_trades["pnl_pct"].mean()
    wins = df_trades[df_trades["hit"] == True]["pnl_pct"]
    losses = df_trades[df_trades["hit"] == False]["pnl_pct"]

    # Sharpe (مبسط، يومي)
    daily_returns = pd.DataFrame(daily_stats)["avg_pnl"] if daily_stats else pd.Series([0])
    sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0

    # Max drawdown (مبسط)
    cumulative = daily_returns.cumsum()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max)
    max_dd = drawdown.min()

    print("\n" + "=" * 60)
    print(f"  نتائج Backtest — {days} يوم, top {top_n}/يوم")
    print("=" * 60)
    print(f"  عدد الصفقات: {len(df_trades)}")
    print(f"  نسبة النجاح: {hit_rate:.1f}%")
    print(f"  متوسط الربح/الصفقة: {avg_pnl:+.2f}%")
    print(f"  متوسط صفقة رابحة: +{wins.mean():.2f}%" if len(wins) > 0 else "  لا رابحات")
    print(f"  متوسط صفقة خاسرة: {losses.mean():.2f}%" if len(losses) > 0 else "  لا خاسرات")
    print(f"  Sharpe (مبسط): {sharpe:.2f}")
    print(f"  Max Drawdown: {max_dd:.2f}%")

    # أفضل الإشارات
    from collections import Counter
    hit_signals = Counter()
    miss_signals = Counter()
    for t in all_trades:
        target = hit_signals if t["hit"] else miss_signals
        for s in t["signals"]:
            target[s] += 1

    print("\n  📊 أداء كل إشارة:")
    for sig in sorted(set(list(hit_signals.keys()) + list(miss_signals.keys()))):
        h = hit_signals.get(sig, 0)
        m = miss_signals.get(sig, 0)
        total = h + m
        rate = h / total * 100 if total > 0 else 0
        print(f"    {sig:20s}: {rate:5.1f}% ({h}/{total})")

    # حفظ
    results = {
        "date": datetime.now().isoformat(),
        "config": {"days": days, "top_n": top_n, "hit_target": hit_target,
                   "hit_stop": hit_stop, "hold_days": hold_days},
        "summary": {
            "total_trades": len(df_trades),
            "hit_rate_pct": round(hit_rate, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "sharpe": round(float(sharpe), 2),
            "max_drawdown_pct": round(float(max_dd), 2),
        },
        "trades": all_trades[-100:],  # آخر 100 لتوفير المساحة
        "daily_stats": daily_stats,
    }

    out = Path("tadawul_data") / "backtest_result.json"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 النتائج: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--target", type=float, default=1.5)
    parser.add_argument("--stop", type=float, default=-2.0)
    parser.add_argument("--hold", type=int, default=3)
    args = parser.parse_args()

    # استخدم نفس الرموز من scanner_v9
    from scanner_v9 import TASI_TICKERS
    tickers = [f"{t}.SR" for t in TASI_TICKERS]

    run_backtest(tickers, days=args.days, top_n=args.top,
                 hit_target=args.target, hit_stop=args.stop, hold_days=args.hold)
