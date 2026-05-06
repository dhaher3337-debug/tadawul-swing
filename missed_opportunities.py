# -*- coding: utf-8 -*-
"""
Missed Opportunities Engine — V9.2.1
=====================================
🎯 الفكرة:
    كل يوم، نحلل أعلى 10 رابحين في السوق:
      - إذا النظام رشحهم → ✅ نجح
      - إذا لم يرشحهم → ❌ فرصة ضائعة - ندرس لماذا

كل فرصة ضائعة = درس مجاني لتحسين النظام.

المخرجات:
  - tadawul_data/missed_opportunities.jsonl (كل سجل بسطر واحد)
  - tadawul_data/missed_summary.json (إحصاءات تجميعية)

مثال على التحليل:
  Stock: 4146 (صناعية)
  Move: +9.96%
  هل رُشّح؟ ❌ لا
  السبب المحتمل:
    - score=18 (تحت MIN_SCORE=20)
    - ADX=22 (ضعيف)
    - vol_ratio=1.4 (ليس استثنائياً)
  Lesson: قطاع الصناعية أعطى حركات قوية - راجع threshold للقطاع

التشغيل:
  من run_all.py بعد scanner_v9 و قبل ai_analyst:
    from missed_opportunities import analyze_missed_today
    analyze_missed_today(stocks_data, candidates)
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, Counter

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
F_MISSED_LOG = BASE / "missed_opportunities.jsonl"
F_MISSED_SUMMARY = BASE / "missed_summary.json"

# استبعاد الأسهم الرخيصة جداً
MIN_PRICE = 5.0


def get_top_gainers(stocks_data, top_n=10):
    """
    احصل على أعلى N رابحين من stocks_data.
    
    Args:
        stocks_data: dict {ticker: DataFrame مع أعمدة OHLCV}
        top_n: عدد الأعلى للإرجاع
    
    Returns:
        list of dict: [{"ticker", "close", "change_pct", "volume"}]
    """
    gainers = []
    for ticker_full, df in stocks_data.items():
        if df is None or df.empty or len(df) < 2:
            continue
        
        try:
            ticker = ticker_full.replace(".SR", "")
            last_close = float(df["Close"].iloc[-1])
            prev_close = float(df["Close"].iloc[-2])
            
            if prev_close <= 0 or last_close < MIN_PRICE:
                continue
            
            change_pct = (last_close - prev_close) / prev_close * 100
            
            volume = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0
            
            # تجاهل الأسهم بـ 0% change (بيانات غير محدثة)
            if abs(change_pct) < 0.001:
                continue
            
            gainers.append({
                "ticker": ticker,
                "close": round(last_close, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "volume": int(volume),
            })
        except Exception as e:
            log.debug(f"Error processing {ticker_full}: {e}")
            continue
    
    # ترتيب حسب أعلى تغير
    gainers.sort(key=lambda x: -x["change_pct"])
    return gainers[:top_n]


def diagnose_miss(gainer, candidate_data=None):
    """
    تشخيص لماذا لم يُرشّح هذا السهم رغم ارتفاعه القوي.
    
    Args:
        gainer: dict بيانات السهم الرابح
        candidate_data: dict إذا كان السهم في قائمة candidates 
                        (مع score منخفض)، None إذا غير موجود تماماً
    
    Returns:
        dict مع reasons قائمة + lesson تفسير
    """
    reasons = []
    lesson = ""
    
    if candidate_data is None:
        # السهم لم يُفحص أصلاً (ليس في قائمة 196 سهم)
        reasons.append("غير موجود في قائمة المراقبة")
        lesson = "السهم خارج قائمة الـ 196 سهم - فكر في توسيع التغطية"
        return {"reasons": reasons, "lesson": lesson, "case": "out_of_universe"}
    
    # السهم في القائمة لكن score منخفض
    score = candidate_data.get("score", 0)
    
    if score < 20:
        reasons.append(f"score منخفض ({score:.1f} < 20)")
    
    # فحص المؤشرات
    adx = candidate_data.get("adx")
    if adx is not None and adx < 25:
        reasons.append(f"ADX ضعيف ({adx:.1f} < 25)")
    
    rsi = candidate_data.get("rsi")
    if rsi is not None:
        if rsi < 40:
            reasons.append(f"RSI منخفض ({rsi:.1f}) - oversold قبل الحركة")
        elif rsi > 70:
            reasons.append(f"RSI overbought ({rsi:.1f}) - النظام تجنب late entry")
    
    vol_ratio = candidate_data.get("volume_ratio")
    if vol_ratio is not None and vol_ratio < 1.5:
        reasons.append(f"حجم عادي قبل الحركة ({vol_ratio:.1f}x)")
    
    mtf = candidate_data.get("mtf_aligned", 0)
    if mtf < 2:
        reasons.append(f"MTF غير محاذٍ ({mtf}/2)")
    
    # تشخيص الحالة
    if score >= 20 and not reasons:
        # لو score مقبول لكن لم يُرشّح، فالمشكلة في top_n cutoff
        reasons.append("تحت top 8 في الترتيب")
        lesson = "النظام رآه لكن أسهم أخرى كانت أقوى. زد top_n إذا تكرر"
        case = "ranked_below_top"
    elif score < 10:
        lesson = "السهم لم يستوفِ الحد الأدنى للإشارة - الحركة كانت مفاجئة (أخبار/تطورات)"
        case = "unforeseeable"
    else:
        lesson = f"النظام شاف ضعف ({len(reasons)} نقطة) لكن السهم تحرك رغم ذلك"
        case = "predictable_miss"
    
    return {
        "reasons": reasons,
        "lesson": lesson,
        "case": case,
        "score": score,
    }


def analyze_missed_today(stocks_data, candidates, today_str=None, top_n=10):
    """
    🎯 الدالة الرئيسية - تُستدعى يومياً من run_all.py
    
    تحلل أعلى N رابحين، تحدد المُرشّحين منهم وغير المُرشّحين،
    وتحفظ تشخيص لكل فرصة ضائعة.
    
    Args:
        stocks_data: dict من scanner_v9 {ticker.SR: DataFrame}
        candidates: list من scanner_v9 (الأسهم المرشحة)
        today_str: تاريخ اليوم
        top_n: عدد الأعلى للتحليل
    
    Returns:
        dict {
            "date", "top_gainers", "caught", "missed",
            "summary": str,
        }
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    # 1. أعلى الرابحين
    top_gainers = get_top_gainers(stocks_data, top_n=top_n)
    
    if not top_gainers:
        log.warning("لا توجد بيانات لتحديد الرابحين")
        return None
    
    # 2. خريطة الـ candidates للوصول السريع
    cand_by_ticker = {c["ticker"]: c for c in candidates}
    
    # ⚠️ مهم: top_picks (top 8 اللي اختارهم النظام)
    top_picks_tickers = {c["ticker"] for c in candidates[:8]}
    
    # 3. لكل رابح، حدّد إذا رُشّح أم لا
    caught_list = []
    missed_list = []
    
    for gainer in top_gainers:
        ticker = gainer["ticker"]
        in_picks = ticker in top_picks_tickers
        in_full_candidates = ticker in cand_by_ticker
        
        analysis = {
            "date": today_str,
            "ticker": ticker,
            "change_pct": gainer["change_pct"],
            "close": gainer["close"],
            "volume": gainer["volume"],
            "in_top_picks": in_picks,
            "in_candidates_list": in_full_candidates,
        }
        
        if in_picks:
            # ✅ النظام اختاره
            cand = cand_by_ticker[ticker]
            analysis["status"] = "caught"
            analysis["score"] = cand.get("score")
            analysis["picked_rank"] = next(
                (i for i, c in enumerate(candidates[:8]) if c["ticker"] == ticker),
                None
            )
            caught_list.append(analysis)
        else:
            # ❌ النظام لم يختره - لماذا؟
            cand_data = cand_by_ticker.get(ticker)
            diag = diagnose_miss(gainer, cand_data)
            analysis["status"] = "missed"
            analysis["diagnosis"] = diag
            if cand_data:
                # نحفظ السياق للتعلم
                analysis["context"] = {
                    "score": cand_data.get("score"),
                    "rsi": cand_data.get("rsi"),
                    "adx": cand_data.get("adx"),
                    "mfi": cand_data.get("mfi"),
                    "volume_ratio": cand_data.get("volume_ratio"),
                    "mtf_aligned": cand_data.get("mtf_aligned"),
                    "weekly_trend": cand_data.get("weekly_trend"),
                }
            missed_list.append(analysis)
    
    # 4. حفظ السجلات
    F_MISSED_LOG.parent.mkdir(parents=True, exist_ok=True)
    
    # نحفظ كل أعلى رابحين في سجل (caught + missed) للتاريخ الكامل
    with open(F_MISSED_LOG, "a", encoding="utf-8") as f:
        for record in caught_list + missed_list:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    # 5. ملخص
    catch_rate = len(caught_list) / len(top_gainers) * 100 if top_gainers else 0
    summary = f"رصدنا {len(caught_list)}/{len(top_gainers)} ({catch_rate:.0f}%) من أعلى الرابحين"
    
    print(f"\n  🎯 تحليل الفرص الضائعة:")
    print(f"     {summary}")
    
    if missed_list:
        print(f"     ❌ {len(missed_list)} فرصة ضائعة:")
        for m in missed_list[:5]:
            print(f"        {m['ticker']}: +{m['change_pct']}% — {m['diagnosis']['lesson'][:60]}")
    
    if caught_list:
        print(f"     ✅ {len(caught_list)} رصدنا:")
        for c in caught_list[:3]:
            print(f"        {c['ticker']}: +{c['change_pct']}% (rank: {c.get('picked_rank', '?')})")
    
    # 6. حفظ ملخص يومي
    daily_record = {
        "date": today_str,
        "top_gainers_count": len(top_gainers),
        "caught_count": len(caught_list),
        "missed_count": len(missed_list),
        "catch_rate_pct": round(catch_rate, 1),
        "missed_tickers": [m["ticker"] for m in missed_list],
        "caught_tickers": [c["ticker"] for c in caught_list],
    }
    
    # تحديث ملخص تجميعي
    update_summary(daily_record)
    
    return {
        "date": today_str,
        "top_gainers": top_gainers,
        "caught": caught_list,
        "missed": missed_list,
        "catch_rate_pct": catch_rate,
        "summary": summary,
    }


def update_summary(daily_record):
    """تحديث الملخص التجميعي عبر الأيام."""
    summary = {}
    if F_MISSED_SUMMARY.exists():
        try:
            with open(F_MISSED_SUMMARY, encoding="utf-8") as f:
                summary = json.load(f)
        except Exception:
            summary = {}
    
    # قسم بحسب التاريخ
    if "by_date" not in summary:
        summary["by_date"] = {}
    summary["by_date"][daily_record["date"]] = daily_record
    
    # احتفظ آخر 30 يوم
    dates = sorted(summary["by_date"].keys())
    if len(dates) > 30:
        for d in dates[:-30]:
            del summary["by_date"][d]
    
    # احصاءات تجميعية
    all_records = list(summary["by_date"].values())
    total_caught = sum(r["caught_count"] for r in all_records)
    total_missed = sum(r["missed_count"] for r in all_records)
    total = total_caught + total_missed
    
    summary["lifetime"] = {
        "days_analyzed": len(all_records),
        "total_top_gainers_seen": total,
        "total_caught": total_caught,
        "total_missed": total_missed,
        "lifetime_catch_rate_pct": round(total_caught / total * 100, 1) if total > 0 else 0,
        "last_updated": datetime.now().isoformat(),
    }
    
    # الأسهم المتكررة في missed (الفرص الضائعة المتكررة!)
    if F_MISSED_LOG.exists():
        missed_counter = Counter()
        case_counter = Counter()
        try:
            with open(F_MISSED_LOG, encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    if rec.get("status") == "missed":
                        missed_counter[rec["ticker"]] += 1
                        if rec.get("diagnosis", {}).get("case"):
                            case_counter[rec["diagnosis"]["case"]] += 1
        except Exception:
            pass
        
        summary["recurring_misses"] = [
            {"ticker": tk, "count": cnt}
            for tk, cnt in missed_counter.most_common(10)
        ]
        summary["miss_cases"] = dict(case_counter)
    
    F_MISSED_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    with open(F_MISSED_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def get_recurring_missed_tickers(min_count=2):
    """
    احصل على الأسهم المتكررة في missed (للاستخدام لاحقاً في تعديل scoring).
    
    إذا سهم تكرر في missed 3+ مرات، النظام يجب أن يخفض threshold له.
    """
    if not F_MISSED_SUMMARY.exists():
        return []
    
    try:
        with open(F_MISSED_SUMMARY, encoding="utf-8") as f:
            summary = json.load(f)
    except Exception:
        return []
    
    return [
        r for r in summary.get("recurring_misses", [])
        if r["count"] >= min_count
    ]


if __name__ == "__main__":
    # اختبار
    import sys
    print("=" * 60)
    print("Missed Opportunities Engine - Status")
    print("=" * 60)
    
    if F_MISSED_SUMMARY.exists():
        with open(F_MISSED_SUMMARY, encoding="utf-8") as f:
            print(json.dumps(json.load(f), ensure_ascii=False, indent=2))
    else:
        print("لم يتم تشغيل التحليل بعد")
