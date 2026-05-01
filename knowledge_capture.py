# -*- coding: utf-8 -*-
"""
Knowledge Capture Engine — V9.2
================================
🎯 الهدف الاستراتيجي:
    حفظ "عقل كلود" يومياً ليمكن استبداله مستقبلاً.

كيف يعمل:
    كل يوم بعد استدعاء Opus، يحفظ سجلاً مفصلاً يحوي:
      - السياق الكامل (50+ متغير: السوق، السهم، المؤشرات)
      - قرار كلود (action, confidence, reasoning)
      - النتيجة الفعلية (تُملأ لاحقاً من paper_trading)

الفلسفة:
    - بعد 6 أشهر = ~5,000 قرار محفوظ
    - بعد 12 شهر = نموذج ML يستطيع محاكاة كلود بدقة 80%+
    - هذا الملف هو "وثيقة الاستقلالية" - كلود يستطيع أن يموت، لكن النظام يستمر

ملف الـ output:
    tadawul_data/claude_decisions_log.jsonl  (JSON Lines - سطر لكل قرار)
    tadawul_data/knowledge_stats.json        (إحصاءات تجميعية)

الاستخدام في run_all.py:
    from knowledge_capture import capture_decisions
    capture_decisions(opus_picks=ai_result['picks'], 
                      candidates=tasi_candidates,
                      macro_context=macro_data)
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
F_DECISIONS_LOG = BASE / "claude_decisions_log.jsonl"
F_KNOWLEDGE_STATS = BASE / "knowledge_stats.json"


def build_full_context(candidate, macro_context, tracker_data=None):
    """
    يبني سياق غني (50+ متغير) لكل سهم.
    هذا السياق هو "فهم النظام للوضع وقت اتخاذ القرار".
    """
    ctx = {
        # ── معلومات السهم الأساسية ──
        "ticker": candidate.get("ticker"),
        "sector": candidate.get("sector"),
        "close": candidate.get("close"),
        
        # ── المؤشرات الفنية ──
        "rsi": candidate.get("rsi"),
        "rsi_14": candidate.get("rsi_14"),
        "stoch_rsi": candidate.get("stoch_rsi"),
        "adx": candidate.get("adx"),
        "atr": candidate.get("atr"),
        "atr_pct": (candidate.get("atr", 0) / candidate.get("close", 1) * 100) if candidate.get("close") else None,
        "mfi": candidate.get("mfi"),
        "obv_trend": candidate.get("obv_trend"),
        "macd_crossover": candidate.get("macd_crossover"),
        
        # ── الحجم والسيولة ──
        "volume_ratio": candidate.get("volume_ratio"),
        "volume_surge": candidate.get("volume_ratio", 0) > 2 if candidate.get("volume_ratio") else False,
        "avg_dollar_volume_5d": candidate.get("avg_dollar_volume_5d"),
        
        # ── Multi-Timeframe ──
        "mtf_aligned": candidate.get("mtf_aligned"),
        "mtf_available": candidate.get("mtf_available"),
        "mtf_multiplier": candidate.get("mtf_multiplier"),
        "weekly_trend": candidate.get("weekly_trend"),
        "daily_trend": candidate.get("daily_trend"),
        
        # ── الأخبار والمشاعر ──
        "news_sentiment": candidate.get("news_sentiment"),
        "news_score": candidate.get("news_score"),
        "news_count": candidate.get("news_count"),
        "news_multiplier": candidate.get("news_multiplier"),
        
        # ── أرباح ──
        "days_to_earnings": candidate.get("days_to_earnings"),
        "earnings_within_5d": (candidate.get("days_to_earnings") or 99) < 5,
        "earnings_multiplier": candidate.get("earnings_multiplier"),
        
        # ── ML ──
        "ml_probability": candidate.get("ml_probability"),
        
        # ── Scoring System ──
        "score": candidate.get("score"),
        "base_score": candidate.get("base_score"),
        "expected_value_pct": candidate.get("expected_value_pct"),
        "risk_reward": candidate.get("risk_reward"),
        "active_signals": candidate.get("signals", []),
        "reasons": candidate.get("reasons", []),
        
        # ── Levels ──
        "stop": candidate.get("stop"),
        "target1": candidate.get("target1"),
        "target2": candidate.get("target2"),
        "stop_pct": ((candidate.get("stop", 0) - candidate.get("close", 1)) / candidate.get("close", 1) * 100) if candidate.get("close") else None,
        "target_pct": ((candidate.get("target1", 0) - candidate.get("close", 1)) / candidate.get("close", 1) * 100) if candidate.get("close") else None,
        
        # ── Relative Strength ──
        "rs_vs_tasi": candidate.get("rs_vs_tasi"),
        
        # ── Inter-market Context ──
        "tasi_close": macro_context.get("tasi_close"),
        "tasi_change_pct": macro_context.get("tasi_change_pct"),
        "tasi_regime": macro_context.get("tasi_regime"),  # bull/bear/sideways
        "oil_brent_close": macro_context.get("oil_brent_close"),
        "oil_change_pct": macro_context.get("oil_change_pct"),
        "usd_sar": macro_context.get("usd_sar"),
        "vix": macro_context.get("vix"),
        "dxy": macro_context.get("dxy"),
        
        # ── Sector Momentum ──
        "sector_momentum_5d": macro_context.get("sectors", {}).get(candidate.get("sector"), {}).get("momentum_5d"),
        "sector_rank_today": macro_context.get("sectors", {}).get(candidate.get("sector"), {}).get("rank_today"),
        
        # ── Stock History (من tracker) ──
        "previous_signals_30d": tracker_data.get("recent_count", 0) if tracker_data else 0,
        "win_rate_recent": tracker_data.get("win_rate_recent", None) if tracker_data else None,
        "last_outcome": tracker_data.get("last_outcome", None) if tracker_data else None,
    }
    
    # تنظيف None values
    return {k: v for k, v in ctx.items() if v is not None}


def capture_opus_decision(ticker, action, confidence, reasoning, expected_move=None, 
                          risk_factors=None, opus_meta=None):
    """
    يبني سجل قرار كلود.
    
    action: "buy" | "skip" | "watch"
    """
    return {
        "action": action,
        "confidence": confidence,
        "reasoning": reasoning[:500] if reasoning else "",  # limit
        "expected_move_pct": expected_move,
        "risk_factors": risk_factors or [],
        "opus_meta": opus_meta or {},  # model name, tokens, etc.
    }


def capture_decisions(opus_picks, candidates, macro_context, tracker=None, today_str=None):
    """
    🔴 الدالة الرئيسية - تُستدعى من run_all.py بعد ai_analyst_v9.
    
    تحفظ:
      - لكل سهم اختاره كلود → سجل "buy" مع كل السياق
      - (اختياري) لكل candidate لم يختره كلود → سجل "skip" 
        (يفيد لتدريب نموذج لاحقاً: ماذا يرفض كلود؟)
    
    بنية الملف: JSONL (سطر JSON لكل قرار) - أفضل للـ append-only
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    # خريطة الـ candidates للوصول السريع
    cand_by_ticker = {c["ticker"]: c for c in candidates}
    
    # tickers التي اختارها كلود
    picked_tickers = {p["ticker"] for p in opus_picks}
    
    F_DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    decisions_today = []
    
    # ════════════════════════════════════════════
    # 1. سجلات لما اختاره كلود (buy decisions)
    # ════════════════════════════════════════════
    for pick in opus_picks:
        ticker = pick["ticker"]
        candidate = cand_by_ticker.get(ticker)
        if not candidate:
            log.warning(f"opus pick {ticker} not in candidates - skipping context build")
            continue
        
        # tracker data للسهم
        tracker_data = None
        if tracker:
            stock_rec = tracker.get("stock_record", {}).get(ticker, {})
            tracker_data = {
                "recent_count": stock_rec.get("total", 0),
                "win_rate_recent": (stock_rec.get("hits", 0) / stock_rec["total"]) if stock_rec.get("total", 0) > 0 else None,
                "last_outcome": stock_rec.get("last_result"),
            }
        
        context = build_full_context(candidate, macro_context, tracker_data)
        decision = capture_opus_decision(
            ticker=ticker,
            action="buy",
            confidence=pick.get("confidence", pick.get("confidence_pct")),
            reasoning=pick.get("reasoning", pick.get("reason", "")),
            expected_move=pick.get("expected_move", candidate.get("expected_value_pct")),
            risk_factors=pick.get("risk_factors", []),
            opus_meta={
                "model": pick.get("model_used", "claude-opus-4-7"),
                "rank": pick.get("rank"),  # ترتيبه في اختيارات كلود
            }
        )
        
        record = {
            "schema_version": "v92.1",
            "date": today_str,
            "captured_at": datetime.now().isoformat(),
            "ticker": ticker,
            "context": context,
            "claude_decision": decision,
            "actual_outcome": None,  # يُملأ لاحقاً من paper_trading
            "outcome_filled_at": None,
        }
        decisions_today.append(record)
    
    # ════════════════════════════════════════════
    # 2. سجلات لما رفضه كلود (top 5 من candidates لم يخترها)
    # هذا يفيد جداً للتعلم: ماذا يكره كلود؟
    # ════════════════════════════════════════════
    skipped_count = 0
    for c in candidates[:15]:  # نأخذ من أعلى 15 candidate
        if c["ticker"] in picked_tickers:
            continue
        if skipped_count >= 5:  # نحفظ 5 سكب فقط (لا نضخّم الملف)
            break
        
        tracker_data = None
        if tracker:
            stock_rec = tracker.get("stock_record", {}).get(c["ticker"], {})
            tracker_data = {
                "recent_count": stock_rec.get("total", 0),
                "win_rate_recent": (stock_rec.get("hits", 0) / stock_rec["total"]) if stock_rec.get("total", 0) > 0 else None,
                "last_outcome": stock_rec.get("last_result"),
            }
        
        context = build_full_context(c, macro_context, tracker_data)
        decision = {
            "action": "skip",
            "confidence": None,
            "reasoning": f"لم يكن ضمن اختيارات كلود رغم score={c.get('score', 0):.1f}",
            "expected_move_pct": None,
            "risk_factors": [],
            "opus_meta": {"model": "claude-opus-4-7"},
        }
        
        record = {
            "schema_version": "v92.1",
            "date": today_str,
            "captured_at": datetime.now().isoformat(),
            "ticker": c["ticker"],
            "context": context,
            "claude_decision": decision,
            "actual_outcome": None,
            "outcome_filled_at": None,
        }
        decisions_today.append(record)
        skipped_count += 1
    
    # ════════════════════════════════════════════
    # 3. حفظ JSONL (append mode)
    # ════════════════════════════════════════════
    with open(F_DECISIONS_LOG, "a", encoding="utf-8") as f:
        for rec in decisions_today:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    
    print(f"  📚 Knowledge Captured: {len([d for d in decisions_today if d['claude_decision']['action']=='buy'])} buys + {skipped_count} skips")
    
    # ════════════════════════════════════════════
    # 4. تحديث الإحصاءات التجميعية
    # ════════════════════════════════════════════
    update_knowledge_stats()
    
    return decisions_today


def update_outcomes_from_paper_trading():
    """
    🔄 دالة تكميلية: تربط الـ outcome الفعلي من paper_trading
    بسجلات Knowledge Capture.
    
    تُستدعى يومياً بعد paper_trading.
    """
    if not F_DECISIONS_LOG.exists():
        return 0
    
    # قراءة paper_trades
    f_trades = BASE / "paper_trades.json"
    if not f_trades.exists():
        return 0
    
    with open(f_trades, encoding="utf-8") as f:
        trades_db = json.load(f)
    
    # خريطة: (ticker, open_date) → outcome
    closed_outcomes = {}
    for t in trades_db.get("closed", []):
        key = (t["ticker"], t["open_date"])
        closed_outcomes[key] = {
            "result": t.get("result"),
            "pnl_pct": t.get("final_pnl_pct"),
            "exit_reason": t.get("exit_reason"),
            "days_held": t.get("days_open"),
            "filled_at": t.get("entry_actual"),
            "exit_at": t.get("exit_price"),
            "mae_pct": t.get("mae_pct"),
            "mfe_pct": t.get("mfe_pct"),
        }
    
    # قراءة كل السجلات
    records = []
    with open(F_DECISIONS_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    
    # تحديث: ربط outcome بسجلات buy التي ليس لها outcome بعد
    updated = 0
    for rec in records:
        if rec.get("claude_decision", {}).get("action") != "buy":
            continue
        if rec.get("actual_outcome") is not None:
            continue
        
        key = (rec["ticker"], rec["date"])
        if key in closed_outcomes:
            rec["actual_outcome"] = closed_outcomes[key]
            rec["outcome_filled_at"] = datetime.now().isoformat()
            updated += 1
    
    # كتابة جديدة (نظراً لأن JSONL لا يدعم edit في المنتصف)
    if updated > 0:
        with open(F_DECISIONS_LOG, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"  🔄 Outcomes synced: {updated} decision(s) updated with actual results")
    
    return updated


def update_knowledge_stats():
    """
    تحديث إحصاءات قاعدة المعرفة - مفيدة لمعرفة "نضوج" النظام.
    """
    if not F_DECISIONS_LOG.exists():
        return
    
    stats = {
        "computed_at": datetime.now().isoformat(),
        "total_decisions": 0,
        "buy_decisions": 0,
        "skip_decisions": 0,
        "with_outcomes": 0,
        "by_date": defaultdict(int),
        "by_ticker": defaultdict(int),
        "by_sector": defaultdict(int),
        "by_action": defaultdict(int),
        "first_decision_date": None,
        "last_decision_date": None,
    }
    
    with open(F_DECISIONS_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            
            stats["total_decisions"] += 1
            d = rec.get("date", "")
            stats["by_date"][d] += 1
            stats["by_ticker"][rec.get("ticker", "?")] += 1
            sector = rec.get("context", {}).get("sector", "?")
            stats["by_sector"][sector] += 1
            
            action = rec.get("claude_decision", {}).get("action", "?")
            stats["by_action"][action] += 1
            if action == "buy":
                stats["buy_decisions"] += 1
            elif action == "skip":
                stats["skip_decisions"] += 1
            
            if rec.get("actual_outcome"):
                stats["with_outcomes"] += 1
            
            if d:
                if not stats["first_decision_date"] or d < stats["first_decision_date"]:
                    stats["first_decision_date"] = d
                if not stats["last_decision_date"] or d > stats["last_decision_date"]:
                    stats["last_decision_date"] = d
    
    # تحويل defaultdict → dict
    stats["by_date"] = dict(stats["by_date"])
    stats["by_ticker"] = dict(sorted(stats["by_ticker"].items(), key=lambda x: -x[1])[:30])
    stats["by_sector"] = dict(stats["by_sector"])
    stats["by_action"] = dict(stats["by_action"])
    
    # مؤشر النضوج
    total = stats["total_decisions"]
    if total < 100:
        stats["maturity"] = "infant"  # < شهر
        stats["readiness_for_distillation"] = "0%"
    elif total < 500:
        stats["maturity"] = "young"  # 1-3 شهور
        stats["readiness_for_distillation"] = "20%"
    elif total < 1500:
        stats["maturity"] = "developing"  # 3-6 شهور
        stats["readiness_for_distillation"] = "50%"
    elif total < 5000:
        stats["maturity"] = "mature"  # 6-12 شهر
        stats["readiness_for_distillation"] = "80%"
    else:
        stats["maturity"] = "expert"  # 1+ سنة
        stats["readiness_for_distillation"] = "100%"
    
    with open(F_KNOWLEDGE_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    return stats


# ════════════════════════════════════════════════
# للاستخدام المستقبلي في V9.3 (Pattern Distillation)
# ════════════════════════════════════════════════
def query_decisions(filters=None, limit=None):
    """
    استعلام مرن على قاعدة المعرفة.
    
    filters: dict مثل:
        {"action": "buy", "ticker": "2223"}
        {"actual_outcome.result": "WIN_T2"}
    
    يستخدم في:
      - V9.3 لتقطير الأنماط
      - V10 لتدريب ML model
    """
    if not F_DECISIONS_LOG.exists():
        return []
    
    results = []
    with open(F_DECISIONS_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            
            if filters:
                match = True
                for key, value in filters.items():
                    # nested keys: "claude_decision.action"
                    parts = key.split(".")
                    cur = rec
                    for p in parts:
                        if isinstance(cur, dict):
                            cur = cur.get(p)
                        else:
                            cur = None
                            break
                    if cur != value:
                        match = False
                        break
                if not match:
                    continue
            
            results.append(rec)
            if limit and len(results) >= limit:
                break
    
    return results


if __name__ == "__main__":
    # عرض الإحصاءات الحالية
    if F_KNOWLEDGE_STATS.exists():
        with open(F_KNOWLEDGE_STATS, encoding="utf-8") as f:
            print(json.dumps(json.load(f), ensure_ascii=False, indent=2))
    else:
        print("لا توجد قاعدة معرفة بعد - شغّل النظام أولاً")
