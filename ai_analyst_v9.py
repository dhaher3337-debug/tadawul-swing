# -*- coding: utf-8 -*-
"""
محلل Claude Opus — V9
==========================
يُزوّد Opus بـ:
  - مرشحين + ML probability لكل سهم
  - ملخص ارتباطات + أزواج catch-up
  - تدفق السيولة لكل قطاع + دوران القطاعات
  - Leader/Laggard لكل قطاع
  - ماكرو حقيقي (بعد إصلاح bug الـ Series)
  - feature_importance من XGBoost
  - مقاييس ML (AUC, precision, recall)

النموذج: claude-opus-4-7 (Opus 4.7) حسب قرار المستخدم الإبقاء على Opus يومياً
"""
import anthropic
import json
import os
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
F_CANDIDATES = BASE / "tasi_candidates.json"
F_AI_RESULT = BASE / "ai_result.json"
F_AI_LOG = BASE / "ai_learning_log.json"


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _format_candidates(candidates, limit=40):
    """يُنسق المرشحين بشكل مضغوط لتوفير tokens."""
    out = []
    for i, c in enumerate(candidates[:limit], 1):
        ml = c.get("ml_probability")
        ml_str = f"ML:{ml*100:.0f}%" if ml is not None else "ML:-"
        out.append(
            f"{i}. {c['ticker']}|{c['sector']}|{c['close']}|{c['change']:+.1f}% "
            f"S:{c['score']} {ml_str} EV:{c.get('expected_value_pct',0):+.1f}% RR:{c.get('risk_reward','-')} "
            f"RSI:{c['rsi']} ADX:{c.get('adx','-')} MFI:{c.get('mfi','-')} CMF:{c.get('cmf','-')} "
            f"Fib:{c.get('fib_pos','-')} RS:{c.get('rs_vs_tasi','-')} "
            f"Vol:{c['volume_ratio']}× VWAP:{c.get('vwap_diff',0):+.1f}% "
            f"Wkly:{c.get('weekly_trend','-')} ST:{c.get('supertrend_dir','-')}\n"
            f"   Stop:{c['stop']} T1:{c['target1']} T2:{c['target2']} ATR:{c.get('atr','-')}\n"
            f"   Signals:{','.join(c['signals'])} | {' • '.join(c['reasons'][:4])}"
        )
    return "\n".join(out)


def _format_gainers(gainers, limit=10):
    return "\n".join(
        f"  {g['ticker']} ({g['sector']}): {g['close']} +{g['change']}%"
        for g in gainers[:limit]
    )


def _format_sectors(sector_summary):
    if not sector_summary:
        return "  لا توجد بيانات"
    sorted_sec = sorted(sector_summary.items(), key=lambda x: -x[1].get("avg_change", 0))
    return "\n".join(
        f"  {sec}: متوسط {info['avg_change']:+.2f}% | مرتفعة {info['pct_gainers']:.0f}% ({info['count']} سهم)"
        for sec, info in sorted_sec
    )


def _format_intermarket(intermarket):
    parts = []

    # أزواج مرتبطة
    pairs = intermarket.get("highly_correlated_pairs", [])[:8]
    if pairs:
        parts.append("🔗 أزواج عالية الارتباط (30 يوم):")
        for a, b, c in pairs:
            parts.append(f"  {a} ↔ {b}: {c}")

    # أزواج متباعدة (فرص catch-up)
    divergent = intermarket.get("divergent_pairs", [])[:8]
    if divergent:
        parts.append("\n🎯 فرص Catch-up (قائد ارتفع، متأخر لم يتحرك بعد):")
        for d in divergent:
            parts.append(
                f"  {d['leader']} قفز {d['leader_chg']:+.1f}% • "
                f"{d['laggard']} فقط {d['laggard_chg']:+.1f}% "
                f"(ارتباط {d['corr']}, فرق {d['spread']:+.1f}%) → {d['laggard']} قد يلحق"
            )

    # تدفق السيولة لكل قطاع
    flows = intermarket.get("sector_flows", {})
    if flows:
        parts.append("\n💰 تدفق السيولة (5 أيام، مليون ريال):")
        sorted_flows = sorted(flows.items(), key=lambda x: -x[1].get("net_flow_5d", 0))
        for sec, info in sorted_flows:
            parts.append(
                f"  {sec}: {info['net_flow_5d']:+,.1f}M | {info['avg_change_5d']:+.1f}% | "
                f"زخم: {info['momentum_trend']} | قائد: {info['leader']} ({info['leader_change']:+.1f}%)"
            )

    # دوران القطاعات
    rotations = intermarket.get("sector_rotation", [])
    if rotations:
        parts.append("\n🔄 دوران قطاعي مكتشف:")
        for r in rotations[:5]:
            parts.append(
                f"  {r['sector']}: {r['rotation']} | أمس {r['prev_change']:+.1f}% → اليوم {r['now_change']:+.1f}%"
            )

    # Leader/Laggard
    ll = intermarket.get("leader_laggard", {})
    opportunities = [(s, i) for s, i in ll.items() if i.get("catch_up_opportunity")]
    if opportunities:
        parts.append("\n⚡ فرص Catch-up داخل القطاع:")
        for sec, info in opportunities[:5]:
            parts.append(
                f"  {sec}: قائد {info['leader']} {info['leader_change']:+.1f}% • "
                f"متأخر {info['laggard']} {info['laggard_change']:+.1f}% (فارق {info['spread']:+.1f}%)"
            )

    return "\n".join(parts) if parts else "  لا بيانات intermarket"


def _format_eval(eval_results, eval_summary):
    if not eval_results:
        return f"  {eval_summary}"
    lines = [f"  الإجمالي: {eval_summary}"]
    for r in eval_results[:20]:
        status = "✅" if r["hit"] else "❌"
        lines.append(
            f"  {status} {r['ticker']}: دخول {r['predicted_close']} | "
            f"أعلى {r['max_high']} ({r['max_pct']:+.1f}%) | أدنى {r['min_low']} ({r['min_pct']:+.1f}%)"
        )
    return "\n".join(lines)


def _format_ml_metrics(ml_metrics, importance):
    if not ml_metrics:
        return "  نموذج ML غير مدرّب بعد (بيانات غير كافية)"

    lines = [
        f"  عينات: {ml_metrics.get('samples_total','-')} | "
        f"نسبة إيجابية: {ml_metrics.get('positive_ratio','-')}",
        f"  AUC: {ml_metrics.get('roc_auc','-')} | "
        f"Accuracy: {ml_metrics.get('accuracy','-')} | "
        f"F1: {ml_metrics.get('f1','-')}",
        f"  Precision: {ml_metrics.get('precision','-')} | "
        f"Recall: {ml_metrics.get('recall','-')}",
    ]

    if importance:
        top_feats = list(importance.items())[:8]
        lines.append("\n  أهم المميزات (XGBoost):")
        for feat, imp in top_feats:
            lines.append(f"    {feat}: {imp}")

    return "\n".join(lines)


def build_prompt(data):
    candidates = data.get("candidates", [])
    gainers = data.get("gainers", [])
    macro = data.get("macro", {})
    eval_summary = data.get("eval_summary", "")
    eval_results = data.get("eval_results", [])
    signal_acc = data.get("signal_accuracy", {})
    sector_summary = data.get("sector_summary", {})
    intermarket = data.get("intermarket", {})
    ml_metrics = data.get("ml_metrics", {})
    importance = data.get("feature_importance", {})

    # دقة كل إشارة (بعد الإصلاح: لن تكون كل الأصفار)
    sig_text = "\n".join(
        f"  {sig}: {acc['rate']*100:.0f}% ({acc['hit']}/{acc['triggered']})"
        for sig, acc in sorted(signal_acc.items(), key=lambda x: -x[1].get("rate", 0))
        if acc.get("triggered", 0) > 0
    ) or "  لا توجد بيانات تقييم بعد"

    # ذاكرة AI
    ai_log = load_json(F_AI_LOG, [])
    memory_text = ""
    if ai_log:
        for entry in ai_log[-5:]:
            if entry.get("learning"):
                memory_text += f"  [{entry.get('date','')}] {entry['learning'][:180]}\n"

    system = """أنت محلل أسهم سعودي محترف بخبرة 20 سنة. تحلل السوق السعودي (تداول) بعمق.

لديك 18 مؤشر فني + نموذج XGBoost يتدرب من 90 يوم + تحليل ارتباطات وتدفق سيولة قطاعي.

مهمتك اليوم:
1. اختيار أفضل 5-10 أسهم مع تقييم نقدي: لماذا هذا السهم وليس غيره؟
2. تحليل دوران القطاعات: من يقود ومن يتأخر ولماذا، وهل نرى تبادل أدوار؟
3. اقتناص فرص Catch-up: القائد تحرك، المتأخر لم يتحرك، ما احتمال لحاقه؟
4. ربط الماكرو بالسوق: النفط والسندات الأمريكية و VIX كيف تؤثر غداً؟
5. مراجعة أداء الأمس: ماذا نجح وماذا فشل ولماذا؟
6. اقتراح تعديلات أوزان بناءً على:
   - دقة كل إشارة الحقيقية (rate)
   - feature importance من XGBoost
   - ملاحظاتك كخبير

قواعد صارمة:
- لا تختر سهماً بـ ML probability < 40% إلا بسبب قوي
- تجاهل المرشحين بـ risk_reward < 1.5
- فضّل الأسهم بـ ADX > 25 (اتجاه قوي) على السوق العرضي
- إذا الأسبوعي هابط: فقط إذا كانت هناك إشارة قوية جداً (إجماع 5+ إشارات)
- استفد من catch-up pairs: اذكر فرص محددة إن وجدت

أجب بـ JSON فقط (بدون backticks ولا نص خارجي):
{
  "market_outlook": "صاعد/هابط/محايد/حذر",
  "market_comment": "تعليق استراتيجي يربط الماكرو والـ TASI والقطاعات القائدة",
  "sector_analysis": "أي قطاع يقود ولماذا؟ هل هناك دوران؟ أي قطاع يتعافى؟",
  "global_impact": "كيف تؤثر الماكرو (نفط، S&P، VIX، سندات 10Y، DXY) على تداول غداً",
  "catch_up_opportunities": "فرص catch-up محددة من بيانات intermarket",
  "picks": [
    {
      "ticker": "XXXX",
      "action": "شراء قوي/شراء/مراقبة",
      "confidence": 75,
      "reason": "سبب مركّز مستند إلى إشارات محددة + ML + السياق القطاعي",
      "stop": 0,
      "target": 0,
      "target2": 0,
      "holding_days": 3,
      "risk_level": "منخفض/متوسط/مرتفع"
    }
  ],
  "weight_suggestions": {"signal_name": 1.05},
  "learning_notes": "ماذا تعلمنا اليوم؟ ما الإشارة التي نقصنا؟ ما يجب تحسينه غداً؟",
  "missed_analysis": "الأسهم التي ارتفعت بقوة ولم تكن في مرشحينا — لماذا فاتتنا؟ ما الإشارة التي لم نلتقطها؟",
  "risks_to_watch": "مخاطر محددة يجب مراقبتها (ماكرو، قطاعي، تقني)"
}"""

    user = f"""=== تحليل السوق السعودي — {data.get('date')} ===

📊 الماكرو والمؤشرات العالمية:
  🛢 النفط (Brent): {macro.get('oil','N/A')} ({macro.get('oil_chg',0):+.2f}%)
  🥇 الذهب: {macro.get('gold','N/A')} ({macro.get('gold_chg',0):+.2f}%)
  📈 S&P 500: {macro.get('sp500','N/A')} ({macro.get('sp500_chg',0):+.2f}%)
  😰 VIX (مؤشر الخوف): {macro.get('vix','N/A')} (مستوى: {macro.get('vix_level',0)})
  📊 سندات أمريكية 10Y: {macro.get('us10y','N/A')} ({macro.get('us10y_chg',0):+.2f}%)
  💵 مؤشر الدولار DXY: {macro.get('dxy','N/A')} ({macro.get('dxy_chg',0):+.2f}%)
  🇸🇦 مؤشر TASI: {macro.get('tasi_index','N/A')} ({macro.get('tasi_chg',0):+.2f}%)

═══════════════════════════════════════════
📋 تقييم توقعات الأمس (بتعريف واقعي: +1.5% في 3 أيام دون ضرب -2%):
{_format_eval(eval_results, eval_summary)}

═══════════════════════════════════════════
🤖 نموذج XGBoost (مُدرّب على 90 يوم):
{_format_ml_metrics(ml_metrics, importance)}

═══════════════════════════════════════════
📊 دقة كل إشارة فنية (بناءً على hits حقيقية):
{sig_text}

═══════════════════════════════════════════
🏭 أداء القطاعات اليوم ({len(sector_summary)} قطاع):
{_format_sectors(sector_summary)}

═══════════════════════════════════════════
🔗 تحليل الارتباطات والسيولة:
{_format_intermarket(intermarket)}

═══════════════════════════════════════════
🔥 أعلى 10 ارتفاعاً (بغض النظر عن النقاط):
{_format_gainers(gainers)}

═══════════════════════════════════════════
🧠 ذاكرة آخر 5 أيام:
{memory_text if memory_text else '  لا توجد ملاحظات سابقة'}

═══════════════════════════════════════════
🎯 المرشحون ({len(candidates)} سهم — مرتبين بالنقاط):
{_format_candidates(candidates, limit=40)}

تعليمات نهائية:
- اختر 5-10 أسهم فقط الأقوى (ليس الأكثر نقاطاً دائماً!)
- اربط كل اختيار بسياق القطاع + ML + الماكرو
- اذكر فرص catch-up محددة إن وجدت
- اقترح تعديلات أوزان محددة للإشارات ضعيفة الأداء
- كن صريحاً في التقييم — لا تجامل
"""

    return system, user


def run():
    data = load_json(F_CANDIDATES)
    if not data:
        print("  ⚠️ لا توجد بيانات مرشحين")
        save_json(F_AI_RESULT, {"error": "no candidates",
                                "date": datetime.now().strftime("%Y-%m-%d")})
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("  ⚠️ ANTHROPIC_API_KEY غير موجود — تخطي AI")
        save_json(F_AI_RESULT, {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "market_outlook": "محايد",
            "market_comment": "AI غير متاح",
            "picks": data.get("candidates", [])[:10],
            "no_ai": True,
        })
        return

    system, user = build_prompt(data)
    print("  🧠 إرسال طلب لـ Claude Opus 4.7...")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-opus-4-7",  # الأحدث — حسب قرار المستخدم
            max_tokens=6000,          # زيادة لأن المخرج JSON أغنى
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        rt = msg.content[0].text.strip()
        # إزالة backticks إن وجدت
        if rt.startswith("```"):
            rt = rt.split("\n", 1)[1]
        if rt.endswith("```"):
            rt = rt.rsplit("```", 1)[0]
        rt = rt.strip()

        result = json.loads(rt)
        result["date"] = datetime.now().strftime("%Y-%m-%d")
        result["model"] = "claude-opus-4-7"

        # إثراء picks بكامل بيانات المرشح (من scanner) — Opus يرجع فقط ticker + reason
        candidates_by_ticker = {c["ticker"]: c for c in data.get("candidates", [])}
        enriched_picks = []
        for pick in result.get("picks", []):
            t = pick.get("ticker", "")
            full = candidates_by_ticker.get(t, {})
            # دمج: بيانات Opus تُعلو على candidate (confidence من Opus، لا score)
            merged = {**full, **pick}
            enriched_picks.append(merged)
        result["picks"] = enriched_picks

        # تكلفة Opus 4.7 (السعر الحالي: $5/M input, $25/M output)
        result["cost_usd"] = round(
            (msg.usage.input_tokens * 5 + msg.usage.output_tokens * 25) / 1_000_000, 4
        )
        result["tokens"] = {
            "input": msg.usage.input_tokens,
            "output": msg.usage.output_tokens,
        }

        save_json(F_AI_RESULT, result)
        print(f"  ✅ Opus اختار {len(result.get('picks',[]))} سهم "
              f"(تكلفة: ${result['cost_usd']}, tokens: {result['tokens']['input']}→{result['tokens']['output']})")

        # سجل التعلم
        ai_log = load_json(F_AI_LOG, [])
        ai_log.append({
            "date": result["date"],
            "outlook": result.get("market_outlook", ""),
            "picks_count": len(result.get("picks", [])),
            "learning": result.get("learning_notes", ""),
            "sector_analysis": result.get("sector_analysis", "")[:500],
            "missed": result.get("missed_analysis", "")[:500],
            "weight_changes": result.get("weight_suggestions", {}),
            "cost": result.get("cost_usd", 0),
        })
        ai_log = ai_log[-120:]
        save_json(F_AI_LOG, ai_log)

        # تطبيق اقتراحات الأوزان بحدود آمنة (تغيير أقصى 15% في اليوم)
        wp = BASE / "tasi_weights.json"
        weights = load_json(wp, {})
        suggestions = result.get("weight_suggestions", {})
        if suggestions and weights:
            applied = 0
            for sig, mult in suggestions.items():
                if sig in weights and isinstance(mult, (int, float)):
                    # حدّ أقصى 15% تغيير
                    mult = max(0.85, min(1.15, mult))
                    weights[sig] = max(0.2, min(3.0, weights[sig] * mult))
                    applied += 1
            save_json(wp, weights)
            print(f"  📊 تحديث {applied} وزن بحدود آمنة")

    except json.JSONDecodeError as e:
        print(f"  ❌ JSON error: {e}")
        save_json(F_AI_RESULT, {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "error": str(e),
            "raw": rt[:800] if 'rt' in dir() else "",
            "picks": data.get("candidates", [])[:10],
        })
    except Exception as e:
        print(f"  ❌ Claude error: {e}")
        save_json(F_AI_RESULT, {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "error": str(e),
            "picks": data.get("candidates", [])[:10],
        })


if __name__ == "__main__":
    run()
