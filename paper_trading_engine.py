# -*- coding: utf-8 -*-
"""
Paper Trading Engine — V9.2.3
================================
العمود الفقري للتعلم الذاتي.

التغييرات في V9.2.3 (vs V9.2.2):
  ✅ Bug 1 (P0): منع فتح نفس السهم مرتين في نفس الجلسة (duplicates)
  ✅ Bug 1 (P0): منع إعادة استدعاء open_trades في نفس اليوم
  ✅ Bug 4 (P0): تسجيل days_held فعلياً بعد الإغلاق
  ✅ P0: ATR-based dynamic stops & targets (إذا توفر ATR في candidate)
  ✅ P1: Trailing stop ATR-based بعد T1 (بدلاً من breakeven الثابت)
  ✅ P0: حفظ snapshot للـ ml_dataset عند الإغلاق (لتدريب ML على بيانات حقيقية)
  ✅ تتبع run history لمنع double-runs

كيف يعمل:
  1. عند توليد إشارة جديدة → فتح "صفقة افتراضية"
  2. كل يوم → فحص كل الصفقات المفتوحة
  3. إغلاق تلقائي عند: ضرب stop / target1 / target2 / انتهاء المدة
  4. حفظ في JSON + تحديث Excel dashboard + تحديث ml_dataset
"""
import json
import logging
import csv
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
PAPER_DIR = Path("paper_trades")
PAPER_DIR.mkdir(parents=True, exist_ok=True)

F_TRADES = BASE / "paper_trades.json"
F_STATS = PAPER_DIR / "stats.json"
F_RUN_LOG = BASE / "paper_runs_log.json"          # سجل تشغيل يومي (لمنع double-run)
# 🔴 V9.3 FIX (P0): كان يكتب في ml_dataset.csv بمخطط أعمدة مختلف عن مخطط
# الملف → 39 صفاً قيمة hit فيها نصوص ("Stop Hit"...) → تدريب ML ينهار بصمت
# منذ 2026-05-18. الآن: مخرجات الصفقات في ملف منفصل خاص بتقييم الاستراتيجية،
# و ml_dataset.csv يُبنى حصرياً من universe snapshots (rebuild_ml_from_universe).
F_PAPER_OUTCOMES = BASE / "paper_outcomes.csv"    # نتائج الصفقات (تقييم استراتيجية)
F_CLOSURE_LOG = BASE / "closures_log.jsonl"       # سجل إغلاقات (audit trail)

# ════════════════════════════════════════════════
# المعلمات (V9.2.3 - مُحدَّثة بناءً على تحليل الأسبوع)
# ════════════════════════════════════════════════
MAX_HOLDING_DAYS_DEFAULT = 6
SLIPPAGE_ENTRY_PCT = 0.002    # 0.2% slippage على الدخول
SLIPPAGE_STOP_PCT = 0.005     # 0.5% slippage على ضرب stop (gap)
SLIPPAGE_TARGET_PCT = 0.001   # 0.1% slippage على ضرب target

# مدد الاحتفاظ حسب نوع الإشارة
HOLDING_DAYS_BY_SIGNAL = {
    "mtf_aligned": 7,
    "breakout": 5,
    "support_bounce": 4,
    "mean_reversion": 3,
    "default": 6,
}

# ATR multipliers (V9.2.3 - جديد)
# تحليل الأسبوع أظهر أن T1=5.16% و T2=8.16% بعيدة جداً → فقط 1 من 25 صفقة ضربت T2
# والآلاف من dollars تُترَك على الطاولة بسبب Max Holding Days
ATR_STOP_MULTIPLIER = 1.5      # stop = entry - 1.5*ATR (أضيق من 5% الثابت)
ATR_T1_MULTIPLIER = 1.2        # 🔴 V9.3: خُفّض من 1.5 — متوسط MFE للصفقات الزمنية
                               # كان 2.57% فقط، وT1≈4.5% لم يُضرب في معظمها
ATR_T2_MULTIPLIER = 3.0        # T2 = entry + 3.0*ATR (≈6-8%)
ATR_TRAILING_MULTIPLIER = 1.5  # بعد T1: trailing = max_high_seen - 1.5*ATR

# ════════════════════════════════════════════════
# 🔴 V9.3 (P1): حماية الربح غير المحقق
# الدليل: 33 صفقة أُغلقت بانتهاء المدة بمتوسط MFE = +2.57% لكن خرجت
# بمتوسط -0.02% — النظام كان يشاهد الربح يتبخر بلا أي رد فعل.
# ════════════════════════════════════════════════
BREAKEVEN_TRIGGER_PCT = 1.5    # إذا بلغ غير المحقق +1.5% → ارفع الوقف لنقطة الدخول
PROFIT_LOCK_MFE_PCT = 2.0      # إذا بلغ MFE خلال الصفقة +2.0% ...
PROFIT_LOCK_GIVEBACK_PCT = 0.3 # ... ثم ارتد السعر إلى ≤ entry+0.3% → خروج "Profit Lock"


def _signal_type_from_signals(signals):
    """يستنتج نوع الإشارة من قائمة الـ active signals."""
    if not signals:
        return "default"
    sig_set = set(signals)
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


# ════════════════════════════════════════════════
# Run History (Bug 1 - منع double-run)
# ════════════════════════════════════════════════
def _load_run_log():
    if not F_RUN_LOG.exists():
        return {}
    try:
        with open(F_RUN_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_run_log(log_dict):
    F_RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(F_RUN_LOG, "w", encoding="utf-8") as f:
        json.dump(log_dict, f, ensure_ascii=False, indent=2)


def _mark_run_for_date(today_str, phase):
    """يسجّل أن phase ('update' أو 'open') قد عملت اليوم."""
    runs = _load_run_log()
    if today_str not in runs:
        runs[today_str] = {}
    runs[today_str][phase] = runs[today_str].get(phase, 0) + 1
    runs[today_str][f"{phase}_last_ts"] = datetime.now().isoformat()
    _save_run_log(runs)
    return runs[today_str][phase]


def _has_run_today(today_str, phase):
    """يتحقق إذا phase معينة عملت اليوم."""
    runs = _load_run_log()
    return runs.get(today_str, {}).get(phase, 0) > 0


# ════════════════════════════════════════════════
# ATR-based dynamic targets (P0 - جديد في V9.2.3)
# ════════════════════════════════════════════════
def _compute_dynamic_levels(entry_price, candidate):
    """
    يحسب stop/T1/T2 ديناميكياً بناءً على ATR إذا متوفر.
    
    Fallback: يستخدم الـ stop/T1/T2 من candidate (الطريقة القديمة).
    
    Returns: (stop, target1, target2, used_atr_bool)
    """
    atr = candidate.get("atr") or candidate.get("atr_14")
    
    if atr and atr > 0:
        # ATR-based (V9.2.3)
        stop = round(entry_price - ATR_STOP_MULTIPLIER * atr, 2)
        target1 = round(entry_price + ATR_T1_MULTIPLIER * atr, 2)
        target2 = round(entry_price + ATR_T2_MULTIPLIER * atr, 2)
        # حد أدنى/أقصى للسلامة
        # stop يجب أن لا يكون أقل من -8% أو أكثر من -1.5%
        max_stop = round(entry_price * 0.985, 2)  # -1.5% min stop
        min_stop = round(entry_price * 0.92, 2)   # -8% max stop
        stop = max(min(stop, max_stop), min_stop)
        # T1 يجب أن يكون على الأقل +1.8% (لتغطية slippage + commission)
        min_t1 = round(entry_price * 1.018, 2)
        target1 = max(target1, min_t1)
        # T2 يجب أن يكون على الأقل R:R 2:1 من stop
        risk = entry_price - stop
        min_t2_rr = round(entry_price + 2.0 * risk, 2)
        target2 = max(target2, min_t2_rr)
        return stop, target1, target2, True
    else:
        # Fallback - الطريقة القديمة
        return (
            candidate.get("stop", round(entry_price * 0.95, 2)),
            candidate.get("target1", round(entry_price * 1.05, 2)),
            candidate.get("target2", round(entry_price * 1.08, 2)),
            False,
        )


def open_trades_from_candidates(candidates, today_str=None, max_open=10,
                                 force=False):
    """
    فتح صفقات افتراضية من candidates اليوم.
    
    التحسينات V9.2.3:
      ✅ Bug 1: منع تكرار نفس السهم في نفس الاستدعاء (new_tickers_this_call)
      ✅ Bug 1: منع إعادة استدعاء open_trades في نفس اليوم (force=True للتجاوز)
      ✅ Bug 1: منع فتح صفقة على سهم له صفقة فُتحت في نفس اليوم سابقاً
      ✅ P0: استخدام ATR-based stops/targets
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    # ✅ Bug 1 fix: منع double-run في نفس اليوم
    if not force and _has_run_today(today_str, "open"):
        log.warning(f"open_trades_from_candidates: تم استدعاؤها مسبقاً اليوم {today_str}. "
                    f"استخدم force=True للتجاوز.")
        print(f"  ⚠️ Paper trading open phase ran already today ({today_str}). Skipping to prevent duplicates.")
        return []
    
    db = load_trades()
    
    # الأسهم التي لها صفقة نشطة
    active_tickers = {t["ticker"] for t in db["active"]}
    
    # ✅ Bug 1 fix: استثناء الأسهم التي فُتحت في نفس اليوم سابقاً (في active أو closed)
    opened_today = {t["ticker"] for t in db["active"] if t.get("open_date") == today_str}
    opened_today |= {t["ticker"] for t in db["closed"] if t.get("open_date") == today_str}
    
    # الأسهم التي أُغلقت بالأمس (no re-entry within 24h)
    yesterday = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    recently_closed = {
        t["ticker"] for t in db["closed"]
        if t.get("close_date") == yesterday
    }
    
    skip_tickers = active_tickers | recently_closed | opened_today
    
    new_trades = []
    new_tickers_this_call = set()  # ✅ Bug 1 fix
    
    for c in candidates:
        if len(db["active"]) + len(new_trades) >= max_open:
            break
        ticker = c.get("ticker")
        if not ticker:
            continue
        # ✅ Bug 1 fix: استثناء الأسهم التي أُضيفت في هذه الجلسة
        if ticker in skip_tickers or ticker in new_tickers_this_call:
            continue
        new_tickers_this_call.add(ticker)
        
        entry_price = c.get("close") or c.get("entry_price")
        if not entry_price or entry_price <= 0:
            log.warning(f"تجاهل {ticker}: entry_price غير صالح ({entry_price})")
            continue
        
        # تطبيق slippage على الدخول
        entry_with_slippage = round(entry_price * (1 + SLIPPAGE_ENTRY_PCT), 2)
        
        # ✅ P0: حساب stops/targets ديناميكياً من ATR
        stop, target1, target2, used_atr = _compute_dynamic_levels(entry_with_slippage, c)
        
        signal_type = _signal_type_from_signals(c.get("signals", []))
        max_days = HOLDING_DAYS_BY_SIGNAL.get(signal_type, MAX_HOLDING_DAYS_DEFAULT)
        
        # حساب R:R الفعلي
        risk_amount = entry_with_slippage - stop
        reward_amount = target1 - entry_with_slippage
        rr_actual = round(reward_amount / risk_amount, 2) if risk_amount > 0 else 0
        
        trade = {
            "id": f"T{db['next_id']:04d}",
            "ticker": ticker,
            "sector": c.get("sector", "?"),
            "open_date": today_str,
            "entry_signal_price": entry_price,
            "entry_actual": entry_with_slippage,
            "stop": stop,
            "target1": target1,
            "target2": target2,
            "atr_at_entry": c.get("atr") or c.get("atr_14"),  # ✅ نحفظ ATR للـ trailing
            "used_atr_levels": used_atr,
            "score": c.get("score", 0),
            "ml_probability": c.get("ml_probability"),
            "expected_value_pct": c.get("expected_value_pct"),
            "risk_reward": rr_actual,
            "signal_type": signal_type,
            "active_signals": c.get("signals", []),
            "mtf_aligned": c.get("mtf_aligned", 0),
            "mtf_multiplier": c.get("mtf_multiplier", 1.0),
            "news_sentiment": c.get("news_sentiment", "no_news"),
            "rsi": c.get("rsi"),
            "adx": c.get("adx"),
            "mfi": c.get("mfi"),
            "volume_ratio": c.get("volume_ratio"),
            "power_score": c.get("power_score"),
            "power_class": c.get("power_class"),
            "sector_flow": c.get("sector_flow"),
            "max_holding_days": max_days,
            # Tracking
            "days_open": 0,
            "current_price": entry_with_slippage,
            "max_high_seen": entry_with_slippage,
            "min_low_seen": entry_with_slippage,
            "mae_pct": 0.0,
            "mfe_pct": 0.0,
            "unrealized_pnl_pct": 0.0,
            "partial_closed": False,
            "remaining_size_pct": 100,
            "stop_at_breakeven": False,
            "trailing_active": False,
            "trailing_level": None,
            # Status
            "status": "ACTIVE",
        }
        new_trades.append(trade)
        db["next_id"] += 1
    
    db["active"].extend(new_trades)
    save_trades(db)
    
    # ✅ تسجيل تشغيل phase
    _mark_run_for_date(today_str, "open")
    
    return new_trades


def update_active_trades(stocks_data, today_str=None, force=False):
    """
    تحديث كل الصفقات النشطة بناءً على بيانات اليوم.
    
    التحسينات V9.2.3:
      ✅ Bug 1: منع double-run في نفس اليوم
      ✅ Bug 4: تسجيل days_held الفعلي
      ✅ P1: Trailing stop ATR-based بعد T1
    
    شروط الإغلاق (بالأولوية):
      1. ضرب stop / trailing (LOSS أو BREAKEVEN/PARTIAL)
      2. ضرب target2 (WIN_FULL)
      3. ضرب target1 (PARTIAL → بيع 50% + تفعيل trailing)
      4. انتهاء المدة (TIME_EXIT)
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    # ✅ Bug 1: منع double-update في نفس اليوم
    if not force and _has_run_today(today_str, "update"):
        log.warning(f"update_active_trades: تم استدعاؤها مسبقاً اليوم {today_str}.")
        print(f"  ⚠️ Paper trading update phase ran already today ({today_str}). Skipping.")
        return []
    
    db = load_trades()
    
    closures_today = []
    still_active = []
    
    for trade in db["active"]:
        ticker = trade["ticker"]
        
        # جلب بيانات اليوم
        df = stocks_data.get(f"{ticker}.SR")
        if df is None or df.empty:
            still_active.append(trade)
            continue
        
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
        mfe = (trade["max_high_seen"] - entry) / entry * 100
        mae = (trade["min_low_seen"] - entry) / entry * 100
        trade["mfe_pct"] = round(mfe, 2)
        trade["mae_pct"] = round(mae, 2)
        trade["unrealized_pnl_pct"] = round((current_close - entry) / entry * 100, 2)
        
        # ════════════════════════════════════════════════
        # ✅ P1: Trailing stop logic (بعد T1)
        # ════════════════════════════════════════════════
        if trade.get("partial_closed"):
            atr = trade.get("atr_at_entry")
            if atr and atr > 0:
                # trailing = max_high - ATR_TRAILING_MULTIPLIER * ATR
                new_trail = trade["max_high_seen"] - ATR_TRAILING_MULTIPLIER * atr
                # trailing لا ينزل أبداً (يصعد فقط)
                current_trail = trade.get("trailing_level") or entry
                trade["trailing_level"] = round(max(current_trail, new_trail), 2)
                trade["trailing_active"] = True
                effective_stop = trade["trailing_level"]
            else:
                # fallback: breakeven
                effective_stop = entry
        else:
            effective_stop = trade["stop"]
            # 🔴 V9.3 (P1-a): رفع الوقف لنقطة الدخول عند بلوغ +BREAKEVEN_TRIGGER_PCT
            # (الوقف يصعد فقط، لا ينزل)
            if not trade.get("stop_at_breakeven") and mfe >= BREAKEVEN_TRIGGER_PCT:
                trade["stop_at_breakeven"] = True
            if trade.get("stop_at_breakeven"):
                effective_stop = max(effective_stop, entry)
        
        target1 = trade["target1"]
        target2 = trade["target2"]
        
        # ════════════════════════════════════════════════
        # شروط الإغلاق
        # ════════════════════════════════════════════════
        
        # 1. ضرب stop / trailing؟
        if day_low <= effective_stop:
            exit_price = effective_stop * (1 - SLIPPAGE_STOP_PCT)
            
            if trade.get("partial_closed"):
                t1_profit_pct = (target1 - entry) / entry * 100
                final_pnl_pct = 0.5 * t1_profit_pct + 0.5 * ((exit_price - entry) / entry * 100)
                if final_pnl_pct > 0:
                    result = "WIN_PARTIAL_TRAIL"
                    exit_reason = "Trailing Stop Hit (after T1)"
                else:
                    result = "WIN_PARTIAL"  # حتى لو نهاية سالبة، فالـ T1 ضُرب
                    exit_reason = "Breakeven/Trail Hit (after T1)"
            else:
                final_pnl_pct = (exit_price - entry) / entry * 100
                # 🔴 V9.3: تمييز خروج حماية الربح عن الخسارة الحقيقية
                if trade.get("stop_at_breakeven"):
                    result = "BREAKEVEN"
                    exit_reason = "Breakeven Stop (profit protection)"
                else:
                    result = "LOSS"
                    exit_reason = "Stop Hit"
            
            _close_trade(trade, today_str, exit_price, final_pnl_pct, result, exit_reason)
            closures_today.append(trade)
            continue
        
        # 2. ضرب target2؟
        if day_high >= target2:
            exit_price = target2 * (1 - SLIPPAGE_TARGET_PCT)
            
            if trade.get("partial_closed"):
                t1_profit_pct = (target1 - entry) / entry * 100
                t2_profit_pct = (exit_price - entry) / entry * 100
                final_pnl_pct = 0.5 * t1_profit_pct + 0.5 * t2_profit_pct
            else:
                final_pnl_pct = (exit_price - entry) / entry * 100
            
            _close_trade(trade, today_str, exit_price, final_pnl_pct, "WIN_T2", "Target2 Hit")
            closures_today.append(trade)
            continue
        
        # 3. ضرب target1؟ (partial close)
        if not trade.get("partial_closed") and day_high >= target1:
            trade["partial_closed"] = True
            trade["stop_at_breakeven"] = True
            trade["remaining_size_pct"] = 50
            trade["partial_close_date"] = today_str
            trade["partial_close_price"] = round(target1 * (1 - SLIPPAGE_TARGET_PCT), 2)
            # تفعيل trailing فوراً
            atr = trade.get("atr_at_entry")
            if atr and atr > 0:
                trade["trailing_level"] = round(trade["max_high_seen"] - ATR_TRAILING_MULTIPLIER * atr, 2)
                trade["trailing_active"] = True
            still_active.append(trade)
            continue
        
        # 🔴 3ب (V9.3): Profit Lock — إذا حقق السهم MFE ≥ +2.0% خلال الصفقة
        # ثم ارتد الإغلاق إلى ≤ entry+0.3% → اخرج الآن بدل انتظار انتهاء المدة.
        # الدليل: 33 صفقة زمنية بمتوسط MFE +2.57% خرجت بمتوسط -0.02%.
        if (not trade.get("partial_closed")
                and mfe >= PROFIT_LOCK_MFE_PCT
                and current_close <= entry * (1 + PROFIT_LOCK_GIVEBACK_PCT / 100)):
            exit_price = current_close
            final_pnl_pct = (exit_price - entry) / entry * 100
            result = "PROFIT_LOCK"
            _close_trade(trade, today_str, exit_price, final_pnl_pct, result,
                         "Profit Lock (MFE giveback)")
            closures_today.append(trade)
            continue

        # 4. انتهاء المدة؟
        if trade["days_open"] >= trade["max_holding_days"]:
            exit_price = current_close
            
            if trade.get("partial_closed"):
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
    
    # ✅ تسجيل تشغيل phase
    _mark_run_for_date(today_str, "update")
    
    # ✅ P0: حفظ إغلاقات اليوم إلى ml_dataset
    for closure in closures_today:
        _append_to_paper_outcomes(closure)
        _log_closure(closure)
    
    return closures_today


def _close_trade(trade, close_date, exit_price, pnl_pct, result, reason):
    """يضيف معلومات الإغلاق إلى trade dict.
    
    ✅ Bug 4 fix: تسجيل days_held الفعلي
    """
    trade["status"] = "CLOSED"
    trade["close_date"] = close_date
    trade["exit_price"] = round(exit_price, 2)
    trade["final_pnl_pct"] = round(pnl_pct, 2)
    trade["result"] = result
    trade["exit_reason"] = reason
    # ✅ Bug 4 fix
    try:
        open_d = datetime.strptime(trade["open_date"], "%Y-%m-%d")
        close_d = datetime.strptime(close_date, "%Y-%m-%d")
        trade["days_held"] = (close_d - open_d).days
    except Exception:
        trade["days_held"] = trade.get("days_open", 0)


# ════════════════════════════════════════════════
# 🔴 V9.3 (P0): Paper outcomes — ملف منفصل بمالك واحد للمخطط
# (كان يكتب في ml_dataset.csv بمخطط مغاير → أفسد تدريب ML منذ 2026-05-18)
# ════════════════════════════════════════════════
PAPER_OUTCOMES_COLUMNS = [
    "open_date", "close_date", "ticker", "sector", "signal_type",
    "score", "mtf_aligned", "rsi", "adx", "mfi", "volume_ratio",
    "atr_at_entry", "power_score", "sector_flow",
    "ml_probability", "expected_value_pct", "risk_reward",
    "mae_pct", "mfe_pct", "days_held", "exit_reason",
    "final_pnl_pct", "result", "hit",  # hit = 1 if final_pnl_pct > 0
]


def _append_to_paper_outcomes(trade):
    """يُضيف صفقة مُغلقة إلى paper_outcomes.csv (تقييم الاستراتيجية)."""
    try:
        F_PAPER_OUTCOMES.parent.mkdir(parents=True, exist_ok=True)
        write_header = not F_PAPER_OUTCOMES.exists()

        row = {c: trade.get(c) for c in PAPER_OUTCOMES_COLUMNS}
        row["hit"] = 1 if (trade.get("final_pnl_pct") or 0) > 0 else 0

        with open(F_PAPER_OUTCOMES, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PAPER_OUTCOMES_COLUMNS,
                                    extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        log.warning(f"فشل تسجيل صفقة في paper_outcomes: {e}")


# اسم قديم للتوافق مع أي استدعاءات متبقية
_append_to_ml_dataset = _append_to_paper_outcomes


def _log_closure(trade):
    """يُضيف صفقة مُغلقة إلى سجل audit trail."""
    try:
        F_CLOSURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(F_CLOSURE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(),
                "id": trade.get("id"),
                "ticker": trade.get("ticker"),
                "open_date": trade.get("open_date"),
                "close_date": trade.get("close_date"),
                "days_held": trade.get("days_held"),
                "result": trade.get("result"),
                "exit_reason": trade.get("exit_reason"),
                "pnl_pct": trade.get("final_pnl_pct"),
                "score": trade.get("score"),
                "ml_probability": trade.get("ml_probability"),
                "power_score": trade.get("power_score"),
            }, ensure_ascii=False) + "\n")
    except Exception as e:
        log.debug(f"closure log write failed: {e}")


# ════════════════════════════════════════════════
# Stats
# ════════════════════════════════════════════════
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
    
    wins = [t for t in closed if t.get("final_pnl_pct", 0) > 0]
    losses = [t for t in closed if t.get("final_pnl_pct", 0) < 0]
    breakeven = [t for t in closed if t.get("final_pnl_pct", 0) == 0]
    
    total = len(closed)
    win_count = len(wins)
    loss_count = len(losses)
    
    win_rate = win_count / total * 100 if total > 0 else 0
    
    avg_win = sum(t["final_pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["final_pnl_pct"] for t in losses) / len(losses) if losses else 0
    
    total_gain = sum(t["final_pnl_pct"] for t in wins)
    total_loss_abs = abs(sum(t["final_pnl_pct"] for t in losses))
    profit_factor = total_gain / total_loss_abs if total_loss_abs > 0 else float('inf')
    
    # Expectancy
    expectancy = ((win_count / total) * avg_win + (loss_count / total) * avg_loss) if total > 0 else 0
    
    best = max(closed, key=lambda x: x.get("final_pnl_pct", 0)) if closed else None
    worst = min(closed, key=lambda x: x.get("final_pnl_pct", 0)) if closed else None
    
    # By signal type
    by_signal = defaultdict(lambda: {"total": 0, "wins": 0, "pnls": []})
    for t in closed:
        st = t.get("signal_type", "default")
        by_signal[st]["total"] += 1
        by_signal[st]["pnls"].append(t.get("final_pnl_pct", 0))
        if t.get("final_pnl_pct", 0) > 0:
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
        by_sector[sec]["pnls"].append(t.get("final_pnl_pct", 0))
        if t.get("final_pnl_pct", 0) > 0:
            by_sector[sec]["wins"] += 1
    
    by_sector_clean = {}
    for sec, d in by_sector.items():
        by_sector_clean[sec] = {
            "total": d["total"],
            "wins": d["wins"],
            "win_rate": round(d["wins"] / d["total"] * 100, 1) if d["total"] > 0 else 0,
            "avg_pnl": round(sum(d["pnls"]) / len(d["pnls"]), 2) if d["pnls"] else 0,
        }
    
    # By stock
    by_stock = defaultdict(lambda: {"total": 0, "wins": 0, "pnls": []})
    for t in closed:
        tk = t["ticker"]
        by_stock[tk]["total"] += 1
        by_stock[tk]["pnls"].append(t.get("final_pnl_pct", 0))
        if t.get("final_pnl_pct", 0) > 0:
            by_stock[tk]["wins"] += 1
    
    tired_stocks = []
    for tk, d in by_stock.items():
        if d["total"] >= 3 and (d["wins"] / d["total"]) < 0.34:
            tired_stocks.append({
                "ticker": tk,
                "total": d["total"],
                "wins": d["wins"],
                "win_rate": round(d["wins"] / d["total"] * 100, 1),
                "avg_pnl": round(sum(d["pnls"]) / len(d["pnls"]), 2),
            })
    
    # By exit reason (مفيد لتقييم Max Holding problem)
    by_exit = defaultdict(lambda: {"total": 0, "wins": 0, "pnls": []})
    for t in closed:
        er = t.get("exit_reason", "?")
        by_exit[er]["total"] += 1
        by_exit[er]["pnls"].append(t.get("final_pnl_pct", 0))
        if t.get("final_pnl_pct", 0) > 0:
            by_exit[er]["wins"] += 1
    
    by_exit_clean = {}
    for er, d in by_exit.items():
        by_exit_clean[er] = {
            "total": d["total"],
            "win_rate": round(d["wins"] / d["total"] * 100, 1) if d["total"] > 0 else 0,
            "avg_pnl": round(sum(d["pnls"]) / len(d["pnls"]), 2) if d["pnls"] else 0,
        }
    
    # By score bucket (P0 - مهم لتحديد threshold)
    score_buckets = [(0, 10), (10, 15), (15, 20), (20, 30), (30, 100)]
    by_score = {}
    for lo, hi in score_buckets:
        bucket_trades = [t for t in closed if lo <= (t.get("score") or 0) < hi]
        if bucket_trades:
            wins_b = [t for t in bucket_trades if t.get("final_pnl_pct", 0) > 0]
            pnls_b = [t.get("final_pnl_pct", 0) for t in bucket_trades]
            by_score[f"{lo}-{hi}"] = {
                "total": len(bucket_trades),
                "win_rate": round(len(wins_b) / len(bucket_trades) * 100, 1),
                "avg_pnl": round(sum(pnls_b) / len(pnls_b), 2),
            }
    
    stats = {
        "computed_at": datetime.now().isoformat(),
        "version": "V9.2.3",
        "total_trades": total,
        "active_trades": len(db["active"]),
        "wins": win_count,
        "losses": loss_count,
        "breakeven": len(breakeven),
        "win_rate_pct": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else None,
        "expectancy_pct": round(expectancy, 3),
        "total_pnl_pct": round(sum(t.get("final_pnl_pct", 0) for t in closed), 2),
        "best_trade": {
            "ticker": best["ticker"],
            "pnl": best.get("final_pnl_pct"),
            "date": best.get("close_date"),
        } if best else None,
        "worst_trade": {
            "ticker": worst["ticker"],
            "pnl": worst.get("final_pnl_pct"),
            "date": worst.get("close_date"),
        } if worst else None,
        "by_signal_type": by_signal_clean,
        "by_sector": by_sector_clean,
        "by_exit_reason": by_exit_clean,
        "by_score_bucket": by_score,
        "tired_stocks": tired_stocks,
    }
    
    F_STATS.parent.mkdir(parents=True, exist_ok=True)
    with open(F_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    return stats


def run_paper_trading_cycle(candidates, stocks_data, today_str=None):
    """
    دورة كاملة لـ paper trading يومياً.
    
    التحسينات V9.2.3:
      ✅ كل phase محمية ضد double-run
      ✅ ATR-based levels (إذا متوفر)
      ✅ Trailing stop بعد T1
      ✅ تسجيل في ml_dataset و closures_log
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n  📊 دورة Paper Trading لـ {today_str} (V9.2.3)")
    
    # 1. تحديث الصفقات النشطة
    closures = update_active_trades(stocks_data, today_str)
    if closures:
        print(f"  ✓ أُغلقت {len(closures)} صفقة اليوم:")
        for t in closures:
            emoji = "✅" if "WIN" in t["result"] else "❌" if "LOSS" in t["result"] else "⏰"
            pnl = t.get("final_pnl_pct", 0)
            days = t.get("days_held", "?")
            print(f"     {emoji} {t['ticker']} ({t.get('sector','?')}): "
                  f"{pnl:+.2f}% in {days}d — {t['exit_reason']}")
    
    # 2. فتح صفقات جديدة
    new_trades = open_trades_from_candidates(candidates, today_str)
    if new_trades:
        print(f"  ✓ فُتحت {len(new_trades)} صفقة جديدة:")
        for t in new_trades:
            atr_tag = " [ATR]" if t.get("used_atr_levels") else ""
            print(f"     🆕 {t['id']} {t['ticker']} ({t.get('sector','?')}) @ {t['entry_actual']} | "
                  f"stop={t['stop']} T1={t['target1']} T2={t['target2']}{atr_tag}")
    
    # 3. الإحصائيات
    stats = compute_stats()
    if stats.get("total_trades", 0) > 0:
        pf = stats.get('profit_factor', 'N/A')
        print(f"  📈 إحصاءات تراكمية: {stats['wins']}/{stats['total_trades']} = "
              f"{stats['win_rate_pct']}% WR | PF={pf} | Expectancy={stats.get('expectancy_pct')}%")
    
    return {
        "closures_today": closures,
        "new_trades": new_trades,
        "stats": stats,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = load_trades()
    print(f"Active trades: {len(db['active'])}")
    print(f"Closed trades: {len(db['closed'])}")
    print(f"Next ID: {db['next_id']}")
    stats = compute_stats()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
