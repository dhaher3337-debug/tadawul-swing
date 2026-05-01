# -*- coding: utf-8 -*-
"""
Paper Trading Engine — V9.2
============================
العمود الفقري للتعلم الذاتي.

كيف يعمل:
  1. عند توليد إشارة جديدة → فتح "صفقة افتراضية"
  2. كل يوم → فحص كل الصفقات المفتوحة (داخل run_all)
  3. إغلاق تلقائي عند: ضرب stop / target1 / target2 / انتهاء المدة
  4. حفظ في JSON + تحديث Excel dashboard

الفلسفة:
  - ليس "backtest" - بل forward paper trading حقيقي
  - يحاكي تنفيذ فوري عند الإغلاق (entry = close سعر يوم الإشارة)
  - يبني قاعدة بيانات تعلّم: 6 أشهر = 1500 صفقة = ML training data كافي

المخرجات:
  - tadawul_data/paper_trades.json (الـ source of truth)
  - paper_trades/dashboard_YYYY-MM-DD.xlsx (للمراجعة البشرية)
  - paper_trades/stats.json (للاستخدام في التقارير اليومية)
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
PAPER_DIR = Path("paper_trades")
PAPER_DIR.mkdir(parents=True, exist_ok=True)

F_TRADES = BASE / "paper_trades.json"
F_STATS = PAPER_DIR / "stats.json"

# ════════════════════════════════════════════════
# المعلمات (قابلة للضبط)
# ════════════════════════════════════════════════
MAX_HOLDING_DAYS_DEFAULT = 7  # الحد الأقصى للاحتفاظ
SLIPPAGE_ENTRY_PCT = 0.002   # 0.2% slippage على الدخول
SLIPPAGE_STOP_PCT = 0.005    # 0.5% slippage على ضرب stop (gap)
SLIPPAGE_TARGET_PCT = 0.001  # 0.1% slippage على ضرب target

# مدد الاحتفاظ حسب نوع الإشارة (من Stock DNA المستقبلي)
HOLDING_DAYS_BY_SIGNAL = {
    "mtf_aligned": 7,
    "breakout": 5,
    "support_bounce": 4,
    "mean_reversion": 3,
    "default": 7,
}


def _signal_type_from_signals(signals):
    """يستنتج نوع الإشارة من قائمة الـ active signals."""
    if not signals:
        return "default"
    sig_set = set(signals)
    # MTF له أولوية لأنه أقوى
    # (لاحظ: MTF aligned يُحسب من mtf_multiplier > 1.0، وليس signal name)
    if "breakout" in sig_set and "volume_surge" in sig_set:
        return "breakout"
    if "rsi" in sig_set or "stoch_rsi" in sig_set or "mfi" in sig_set:
        return "mean_reversion"
    if "fibonacci" in sig_set or "vwap" in sig_set:
        return "support_bounce"
    return "default"


def load_trades():
    """تحميل قاعدة بيانات الصفقات."""
    if not F_TRADES.exists():
        return {"active": [], "closed": [], "next_id": 1}
    try:
        with open(F_TRADES, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"فشل تحميل paper_trades.json: {e} — البدء من جديد")
        return {"active": [], "closed": [], "next_id": 1}


def save_trades(trades):
    """حفظ قاعدة بيانات الصفقات."""
    F_TRADES.parent.mkdir(parents=True, exist_ok=True)
    with open(F_TRADES, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)


def open_trades_from_candidates(candidates, today_str=None, max_open=10):
    """
    فتح صفقات افتراضية من candidates اليوم.
    
    منطق:
      - فقط أعلى N candidates (max_open)
      - تجنب فتح صفقة على سهم له صفقة نشطة بالفعل
      - تجنب re-entry خلال 24 ساعة من إغلاق صفقة على نفس السهم
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    db = load_trades()
    
    # الأسهم التي لها صفقة نشطة
    active_tickers = {t["ticker"] for t in db["active"]}
    
    # الأسهم التي أُغلقت بالأمس (no re-entry within 24h)
    yesterday = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    recently_closed = {
        t["ticker"] for t in db["closed"]
        if t.get("close_date") == yesterday
    }
    
    skip_tickers = active_tickers | recently_closed
    
    new_trades = []
    for c in candidates:
        if len(db["active"]) + len(new_trades) >= max_open:
            break
        ticker = c["ticker"]
        if ticker in skip_tickers:
            continue
        
        entry_price = c["close"]
        # تطبيق slippage على الدخول
        entry_with_slippage = round(entry_price * (1 + SLIPPAGE_ENTRY_PCT), 2)
        
        signal_type = _signal_type_from_signals(c.get("signals", []))
        max_days = HOLDING_DAYS_BY_SIGNAL.get(signal_type, MAX_HOLDING_DAYS_DEFAULT)
        
        trade = {
            "id": f"T{db['next_id']:04d}",
            "ticker": ticker,
            "sector": c.get("sector", "?"),
            "open_date": today_str,
            "entry_signal_price": entry_price,
            "entry_actual": entry_with_slippage,
            "stop": c["stop"],
            "target1": c["target1"],
            "target2": c["target2"],
            "score": c.get("score", 0),
            "ml_probability": c.get("ml_probability"),
            "expected_value_pct": c.get("expected_value_pct"),
            "risk_reward": c.get("risk_reward"),
            "signal_type": signal_type,
            "active_signals": c.get("signals", []),
            "mtf_aligned": c.get("mtf_aligned", 0),
            "mtf_multiplier": c.get("mtf_multiplier", 1.0),
            "news_sentiment": c.get("news_sentiment", "no_news"),
            "rsi": c.get("rsi"),
            "adx": c.get("adx"),
            "mfi": c.get("mfi"),
            "volume_ratio": c.get("volume_ratio"),
            "max_holding_days": max_days,
            # Tracking
            "days_open": 0,
            "current_price": entry_with_slippage,
            "max_high_seen": entry_with_slippage,
            "min_low_seen": entry_with_slippage,
            "mae_pct": 0.0,  # Max Adverse Excursion
            "mfe_pct": 0.0,  # Max Favorable Excursion
            "unrealized_pnl_pct": 0.0,
            "partial_closed": False,  # تم بيع 50% عند target1
            "remaining_size_pct": 100,  # نسبة الحجم المتبقية
            "stop_at_breakeven": False,  # نقل stop لـ breakeven بعد target1
            # Status
            "status": "ACTIVE",
        }
        new_trades.append(trade)
        db["next_id"] += 1
    
    db["active"].extend(new_trades)
    save_trades(db)
    
    return new_trades


def update_active_trades(stocks_data, today_str=None):
    """
    تحديث كل الصفقات النشطة بناءً على بيانات اليوم.
    
    stocks_data: dict {ticker: dataframe with OHLCV}
    
    شروط الإغلاق (بالأولوية):
      1. ضرب stop (LOSS أو BREAKEVEN إذا partial_closed)
      2. ضرب target2 (WIN_FULL)
      3. ضرب target1 (PARTIAL → بيع 50% + نقل stop لـ breakeven)
      4. انتهاء المدة (TIME_EXIT)
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    db = load_trades()
    
    closures_today = []
    still_active = []
    
    for trade in db["active"]:
        ticker = trade["ticker"]
        
        # جلب بيانات اليوم
        df = stocks_data.get(f"{ticker}.SR")
        if df is None or df.empty:
            # ما قدرنا نجلب البيانات - نتركها active
            still_active.append(trade)
            continue
        
        # السعر الحالي = آخر إغلاق
        try:
            last_row = df.iloc[-1]
            current_close = float(last_row["Close"])
            day_high = float(last_row["High"])
            day_low = float(last_row["Low"])
        except Exception as e:
            log.debug(f"تحديث {ticker}: {e}")
            still_active.append(trade)
            continue
        
        # تحديث tracking
        trade["current_price"] = round(current_close, 2)
        trade["days_open"] += 1
        if day_high > trade["max_high_seen"]:
            trade["max_high_seen"] = round(day_high, 2)
        if day_low < trade["min_low_seen"]:
            trade["min_low_seen"] = round(day_low, 2)
        
        entry = trade["entry_actual"]
        # MAE / MFE
        mfe = (trade["max_high_seen"] - entry) / entry * 100
        mae = (trade["min_low_seen"] - entry) / entry * 100
        trade["mfe_pct"] = round(mfe, 2)
        trade["mae_pct"] = round(mae, 2)
        trade["unrealized_pnl_pct"] = round((current_close - entry) / entry * 100, 2)
        
        # ════════════════════════════════════════════════
        # شروط الإغلاق (الترتيب مهم!)
        # ════════════════════════════════════════════════
        
        stop = trade["stop"]
        # إذا partial_closed، الـ stop = breakeven
        effective_stop = entry if trade.get("stop_at_breakeven") else stop
        
        target1 = trade["target1"]
        target2 = trade["target2"]
        
        # 1. ضرب stop؟ (نتحقق أولاً لأنه الأكثر تحفظاً)
        if day_low <= effective_stop:
            exit_price = effective_stop * (1 - SLIPPAGE_STOP_PCT)
            
            if trade.get("partial_closed"):
                # كان قد بيع 50%، الباقي يخرج عند breakeven
                # P&L = 50% * (target1 profit) + 50% * 0 (breakeven exit)
                t1_profit_pct = (target1 - entry) / entry * 100
                final_pnl_pct = 0.5 * t1_profit_pct + 0.5 * ((exit_price - entry) / entry * 100)
                result = "WIN_PARTIAL"
                exit_reason = "Stop Hit at Breakeven (after T1)"
            else:
                final_pnl_pct = (exit_price - entry) / entry * 100
                result = "LOSS"
                exit_reason = "Stop Hit"
            
            _close_trade(trade, today_str, exit_price, final_pnl_pct, result, exit_reason)
            closures_today.append(trade)
            continue
        
        # 2. ضرب target2؟ (إغلاق كامل WIN)
        if day_high >= target2:
            exit_price = target2 * (1 - SLIPPAGE_TARGET_PCT)
            
            if trade.get("partial_closed"):
                # 50% خرجت عند target1، 50% الباقية تخرج عند target2
                t1_profit_pct = (target1 - entry) / entry * 100
                t2_profit_pct = (exit_price - entry) / entry * 100
                final_pnl_pct = 0.5 * t1_profit_pct + 0.5 * t2_profit_pct
            else:
                # كل الصفقة تخرج عند target2
                final_pnl_pct = (exit_price - entry) / entry * 100
            
            _close_trade(trade, today_str, exit_price, final_pnl_pct, "WIN_T2", "Target2 Hit")
            closures_today.append(trade)
            continue
        
        # 3. ضرب target1؟ (partial close: بيع 50% + نقل stop لـ breakeven)
        if not trade.get("partial_closed") and day_high >= target1:
            # لا نغلق كاملاً - نسجل partial close
            trade["partial_closed"] = True
            trade["stop_at_breakeven"] = True
            trade["remaining_size_pct"] = 50
            trade["partial_close_date"] = today_str
            trade["partial_close_price"] = round(target1 * (1 - SLIPPAGE_TARGET_PCT), 2)
            still_active.append(trade)
            continue
        
        # 4. انتهاء المدة؟
        if trade["days_open"] >= trade["max_holding_days"]:
            exit_price = current_close
            
            if trade.get("partial_closed"):
                # 50% خرجت عند T1، 50% تخرج بسعر السوق
                t1_profit_pct = (target1 - entry) / entry * 100
                final_pnl_pct = 0.5 * t1_profit_pct + 0.5 * ((exit_price - entry) / entry * 100)
                result = "WIN_PARTIAL"
            else:
                final_pnl_pct = (exit_price - entry) / entry * 100
                result = "TIME_WIN" if final_pnl_pct > 0 else "TIME_LOSS"
            
            _close_trade(trade, today_str, exit_price, final_pnl_pct, result, "Max Holding Days Reached")
            closures_today.append(trade)
            continue
        
        # 5. ما زالت نشطة
        still_active.append(trade)
    
    # تحديث القاعدة
    db["active"] = still_active
    db["closed"].extend(closures_today)
    save_trades(db)
    
    return closures_today


def _close_trade(trade, close_date, exit_price, pnl_pct, result, reason):
    """يضيف معلومات الإغلاق إلى trade dict."""
    trade["status"] = "CLOSED"
    trade["close_date"] = close_date
    trade["exit_price"] = round(exit_price, 2)
    trade["final_pnl_pct"] = round(pnl_pct, 2)
    trade["result"] = result
    trade["exit_reason"] = reason


def compute_stats():
    """حساب إحصائيات الأداء من الصفقات المغلقة."""
    db = load_trades()
    closed = db["closed"]
    
    if not closed:
        return {
            "total_trades": 0,
            "active_trades": len(db["active"]),
            "message": "لا توجد صفقات مغلقة بعد",
        }
    
    wins = [t for t in closed if t["result"] in ("WIN_T1", "WIN_T2", "WIN_PARTIAL", "TIME_WIN")]
    losses = [t for t in closed if t["result"] in ("LOSS", "TIME_LOSS")]
    
    total = len(closed)
    win_count = len(wins)
    loss_count = len(losses)
    
    win_rate = win_count / total * 100 if total > 0 else 0
    
    # Avg win / Avg loss
    avg_win = sum(t["final_pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["final_pnl_pct"] for t in losses) / len(losses) if losses else 0
    
    # Profit factor
    total_gain = sum(t["final_pnl_pct"] for t in wins)
    total_loss_abs = abs(sum(t["final_pnl_pct"] for t in losses))
    profit_factor = total_gain / total_loss_abs if total_loss_abs > 0 else float('inf')
    
    # Best / worst
    best = max(closed, key=lambda x: x["final_pnl_pct"]) if closed else None
    worst = min(closed, key=lambda x: x["final_pnl_pct"]) if closed else None
    
    # By signal type
    by_signal = defaultdict(lambda: {"total": 0, "wins": 0, "pnls": []})
    for t in closed:
        st = t.get("signal_type", "default")
        by_signal[st]["total"] += 1
        by_signal[st]["pnls"].append(t["final_pnl_pct"])
        if t["result"] in ("WIN_T1", "WIN_T2", "WIN_PARTIAL", "TIME_WIN"):
            by_signal[st]["wins"] += 1
    
    by_signal_clean = {}
    for st, d in by_signal.items():
        by_signal_clean[st] = {
            "total": d["total"],
            "wins": d["wins"],
            "win_rate": round(d["wins"] / d["total"] * 100, 1) if d["total"] > 0 else 0,
            "avg_pnl": round(sum(d["pnls"]) / len(d["pnls"]), 2) if d["pnls"] else 0,
        }
    
    # By sector
    by_sector = defaultdict(lambda: {"total": 0, "wins": 0, "pnls": []})
    for t in closed:
        sec = t.get("sector", "?")
        by_sector[sec]["total"] += 1
        by_sector[sec]["pnls"].append(t["final_pnl_pct"])
        if t["result"] in ("WIN_T1", "WIN_T2", "WIN_PARTIAL", "TIME_WIN"):
            by_sector[sec]["wins"] += 1
    
    by_sector_clean = {}
    for sec, d in by_sector.items():
        by_sector_clean[sec] = {
            "total": d["total"],
            "wins": d["wins"],
            "win_rate": round(d["wins"] / d["total"] * 100, 1) if d["total"] > 0 else 0,
            "avg_pnl": round(sum(d["pnls"]) / len(d["pnls"]), 2) if d["pnls"] else 0,
        }
    
    # By stock (للكشف عن "المُتعِبين")
    by_stock = defaultdict(lambda: {"total": 0, "wins": 0, "pnls": []})
    for t in closed:
        tk = t["ticker"]
        by_stock[tk]["total"] += 1
        by_stock[tk]["pnls"].append(t["final_pnl_pct"])
        if t["result"] in ("WIN_T1", "WIN_T2", "WIN_PARTIAL", "TIME_WIN"):
            by_stock[tk]["wins"] += 1
    
    # الأسهم المتعِبة (3+ صفقات بـ win rate < 30%)
    tired_stocks = []
    for tk, d in by_stock.items():
        if d["total"] >= 3 and (d["wins"] / d["total"]) < 0.3:
            tired_stocks.append({
                "ticker": tk,
                "total": d["total"],
                "wins": d["wins"],
                "win_rate": round(d["wins"] / d["total"] * 100, 1),
            })
    
    stats = {
        "computed_at": datetime.now().isoformat(),
        "total_trades": total,
        "active_trades": len(db["active"]),
        "wins": win_count,
        "losses": loss_count,
        "win_rate_pct": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else None,
        "total_pnl_pct": round(sum(t["final_pnl_pct"] for t in closed), 2),
        "best_trade": {
            "ticker": best["ticker"],
            "pnl": best["final_pnl_pct"],
            "date": best.get("close_date"),
        } if best else None,
        "worst_trade": {
            "ticker": worst["ticker"],
            "pnl": worst["final_pnl_pct"],
            "date": worst.get("close_date"),
        } if worst else None,
        "by_signal_type": by_signal_clean,
        "by_sector": by_sector_clean,
        "tired_stocks": tired_stocks,
    }
    
    F_STATS.parent.mkdir(parents=True, exist_ok=True)
    with open(F_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    return stats


def run_paper_trading_cycle(candidates, stocks_data, today_str=None):
    """
    دورة كاملة لـ paper trading يومياً.
    تُستدعى من run_all.py بعد scanner.
    
    المراحل:
      1. تحديث الصفقات النشطة (إغلاق ما يجب)
      2. فتح صفقات جديدة من candidates
      3. حساب الإحصائيات
      4. (لاحقاً) بناء Excel dashboard
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n  📊 دورة Paper Trading لـ {today_str}")
    
    # 1. تحديث الصفقات النشطة
    closures = update_active_trades(stocks_data, today_str)
    if closures:
        print(f"  ✓ أُغلقت {len(closures)} صفقة اليوم:")
        for t in closures:
            emoji = "✅" if "WIN" in t["result"] else "❌" if "LOSS" in t["result"] else "⏰"
            print(f"     {emoji} {t['ticker']} ({t['sector']}): {t['final_pnl_pct']:+.2f}% — {t['exit_reason']}")
    
    # 2. فتح صفقات جديدة
    new_trades = open_trades_from_candidates(candidates, today_str)
    if new_trades:
        print(f"  ✓ فُتحت {len(new_trades)} صفقة جديدة:")
        for t in new_trades:
            print(f"     🆕 {t['id']} {t['ticker']} ({t['sector']}) @ {t['entry_actual']} | stop={t['stop']} T1={t['target1']}")
    
    # 3. الإحصائيات
    stats = compute_stats()
    if stats.get("total_trades", 0) > 0:
        print(f"  📈 إحصاءات تراكمية: {stats['wins']}/{stats['total_trades']} = {stats['win_rate_pct']}% win rate | PF={stats.get('profit_factor', 'N/A')}")
    
    return {
        "closures_today": closures,
        "new_trades": new_trades,
        "stats": stats,
    }


if __name__ == "__main__":
    # اختبار سريع
    logging.basicConfig(level=logging.INFO)
    db = load_trades()
    print(f"Active trades: {len(db['active'])}")
    print(f"Closed trades: {len(db['closed'])}")
    print(f"Next ID: {db['next_id']}")
    stats = compute_stats()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
