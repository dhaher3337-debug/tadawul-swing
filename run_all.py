# -*- coding: utf-8 -*-
"""
المنسق الرئيسي — V9.2
==========================
يشغّل بالتسلسل:
  1. scanner_v9       — مسح + تقييم + ML + ارتباطات
  2. ai_analyst_v9    — تحليل Claude Opus 4.7
  3. paper_trading    — 🔴 V9.2: تتبع الصفقات الافتراضية
  4. build_reports_v9 — بناء HTML
  5. build_dashboard  — 🔴 V9.2: بناء Excel للـ paper trading
  6. أرشفة الأوزان

يُشغَّل عبر GitHub Actions:
  - الأحد-الخميس: 5 صباحاً سعودي (تحليل لافتتاح اليوم)
  - الجمعة: 5 صباحاً سعودي (تحليل ختامي للأسبوع - إغلاق الخميس)
  - السبت: عطلة (لا تشغيل)
"""
import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path

import scanner_v9
import ai_analyst_v9
import build_reports_v9

# 🔴 V9.2: paper trading + knowledge capture
try:
    import paper_trading_engine
    import paper_trading_excel
    import knowledge_capture  # ⭐ العمود الفقري للاستقلالية المستقبلية
    PAPER_TRADING_AVAILABLE = True
    KNOWLEDGE_CAPTURE_AVAILABLE = True
except ImportError as e:
    PAPER_TRADING_AVAILABLE = False
    KNOWLEDGE_CAPTURE_AVAILABLE = False
    print(f"⚠️ ميزات V9.2 غير متاحة: {e}")

BASE = Path("tadawul_data")
WEIGHTS_HIST = Path("weights_history")
PAPER_DIR = Path("paper_trades")

for d in [WEIGHTS_HIST, PAPER_DIR]:
    try:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass


def archive_weights():
    """أرشفة ملف الأوزان اليومي + سجل تراكمي."""
    weights_file = BASE / "tasi_weights.json"
    if not weights_file.exists():
        return

    today = datetime.now().strftime("%Y-%m-%d")
    daily_copy = WEIGHTS_HIST / f"weights_{today}.json"
    shutil.copy(weights_file, daily_copy)

    log_file = WEIGHTS_HIST / "history.jsonl"
    with open(weights_file, encoding="utf-8") as f:
        weights = json.load(f)

    entry = {"date": today, "weights": weights}
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def archive_ml_metrics():
    """نسخة يومية من مقاييس ML لمتابعة التحسن عبر الزمن."""
    metrics_file = BASE / "ml_metrics.json"
    if not metrics_file.exists():
        return
    today = datetime.now().strftime("%Y-%m-%d")
    dest = WEIGHTS_HIST / f"ml_metrics_{today}.json"
    shutil.copy(metrics_file, dest)


def is_friday_close_report():
    """
    هل اليوم جمعة؟ (تقرير ختام الأسبوع - تحليل إغلاق الخميس)
    weekday(): Monday=0, ..., Friday=4, Saturday=5, Sunday=6
    """
    return datetime.now().weekday() == 4  # الجمعة


def main():
    start = datetime.now()
    is_friday = is_friday_close_report()
    
    print(f"\n{'═'*60}")
    if is_friday:
        print(f"  🚀 ماسح تداول V9.2 — تقرير ختام الأسبوع")
    else:
        print(f"  🚀 ماسح تداول V9.2 — التحليل اليومي")
    print(f"  📅 {start:%Y-%m-%d %H:%M:%S} ({start.strftime('%A')})")
    print(f"{'═'*60}")

    # ════════════════════════════════════════════════
    # [1/7] المسح + التقييم + ML
    # ════════════════════════════════════════════════
    print("\n[1/7] 🔍 المسح الفني + تقييم الأمس + تدريب ML")
    print("─" * 60)
    
    candidates = []
    stocks_data = {}
    
    try:
        result = scanner_v9.run()
        if result and isinstance(result, tuple):
            # V9.2.1: scanner_v9.run() يرجع 6 قيم الآن:
            # (candidates, gainers, macro, sector_summary, intermarket, stocks_data)
            if len(result) >= 1:
                candidates = result[0] if result[0] else []
            if len(result) >= 6:
                stocks_data = result[5] if result[5] else {}
                print(f"  ✓ استلمت stocks_data: {len(stocks_data)} سهم لـ paper_trading")
    except Exception as e:
        print(f"  ❌ فشل المسح: {e}")
        traceback.print_exc()
        return 1

    # تحميل candidates و stocks_data من المخرجات
    try:
        with open(BASE / "tasi_candidates.json", encoding="utf-8") as f:
            cand_data = json.load(f)
        candidates = cand_data.get("candidates", [])
    except Exception as e:
        print(f"  ⚠️ فشل تحميل candidates للـ paper trading: {e}")

    # ════════════════════════════════════════════════
    # [2/7] Claude Opus
    # ════════════════════════════════════════════════
    print("\n[2/7] 🧠 تحليل Claude Opus 4.7")
    print("─" * 60)
    try:
        ai_analyst_v9.run()
    except Exception as e:
        print(f"  ⚠️ فشل AI (نكمل بدون): {e}")

    # ════════════════════════════════════════════════
    # [3/7] 🔴 V9.2 Knowledge Capture
    # حفظ "عقل كلود" للاستقلالية المستقبلية
    # ════════════════════════════════════════════════
    print("\n[3/7] 📚 Knowledge Capture (V9.2)")
    print("─" * 60)
    
    if KNOWLEDGE_CAPTURE_AVAILABLE:
        try:
            # تحميل المخرجات
            ai_result_file = BASE / "ai_result.json"
            if ai_result_file.exists():
                with open(ai_result_file, encoding="utf-8") as f:
                    ai_result = json.load(f)
                
                opus_picks = ai_result.get("picks", [])
                
                # tracker الحالي
                tracker_file = BASE / "tasi_tracker.json"
                tracker = {}
                if tracker_file.exists():
                    with open(tracker_file, encoding="utf-8") as f:
                        tracker = json.load(f)
                
                # 🔴 V9.2 FIX: تحميل macro من tasi_candidates.json بشكل صحيح
                macro_context = {}
                cand_file = BASE / "tasi_candidates.json"
                if cand_file.exists():
                    try:
                        with open(cand_file, encoding="utf-8") as f:
                            cand_full = json.load(f)
                        macro_context = cand_full.get("macro", {})
                    except Exception as e:
                        print(f"  ⚠️ فشل تحميل macro context: {e}")
                
                if opus_picks and not ai_result.get("no_ai"):
                    knowledge_capture.capture_decisions(
                        opus_picks=opus_picks,
                        candidates=candidates,
                        macro_context=macro_context,
                        tracker=tracker,
                    )
                else:
                    print("  ⚠️ لا توجد قرارات من كلود لحفظها (no_ai mode)")
        except Exception as e:
            print(f"  ⚠️ فشل knowledge capture: {e}")
            import traceback
            traceback.print_exc()
    
    # ════════════════════════════════════════════════
    # [4/6] 🔴 V9.2 Paper Trading
    # ════════════════════════════════════════════════
    # ════════════════════════════════════════════════
    # [4/7] 🆕 V9.2.2: Missed Opportunities Analysis
    # تحليل أعلى الرابحين والفرص الضائعة
    # ════════════════════════════════════════════════
    print("\n[4/7] 🎯 تحليل الفرص الضائعة (V9.2.2)")
    print("─" * 60)
    
    try:
        import missed_opportunities
        if stocks_data and candidates:
            mo_result = missed_opportunities.analyze_missed_today(
                stocks_data=stocks_data,
                candidates=candidates,
            )
            if mo_result:
                # سجّل ملخص في knowledge stats
                catch_rate = mo_result.get('catch_rate_pct', 0)
                missed_count = len(mo_result.get('missed', []))
                print(f"  📈 catch rate اليوم: {catch_rate}%")
                if missed_count > 0:
                    print(f"  ⚠️ {missed_count} فرصة ضائعة محفوظة للتعلم")
        else:
            print("  ⚠️ لا توجد بيانات لتحليل الفرص الضائعة")
    except ImportError:
        print("  ⚠️ missed_opportunities module غير متاح")
    except Exception as e:
        print(f"  ⚠️ فشل تحليل الفرص الضائعة: {e}")
        traceback.print_exc()
    
    # ════════════════════════════════════════════════
    # [5/7] 🔴 V9.2 Paper Trading
    # ════════════════════════════════════════════════
    print("\n[5/7] 📊 Paper Trading (V9.2)")
    print("─" * 60)
    
    if PAPER_TRADING_AVAILABLE and candidates:
        try:
            # 🔴 V9.2.1: نستخدم stocks_data من scanner مباشرة
            # المشكلة السابقة: كنا نجلب period_days=10 وهذا أقل من 50 (الحد الأدنى)
            # فالـ batch fetch كان يرجع dict فارغ، فلا تتحدث الصفقات
            
            # تأكد من حداثة البيانات قبل التحديث
            if stocks_data:
                print(f"  ✓ استخدام stocks_data من scanner ({len(stocks_data)} سهم)")
            else:
                print(f"  ⚠️ stocks_data فارغة - الصفقات لن تتحدث!")
            
            # نمرّر فقط top candidates لاختيار جديد (top 10)
            top_picks = candidates[:10]
            
            paper_result = paper_trading_engine.run_paper_trading_cycle(
                top_picks, stocks_data
            )
            
            # 🔴 ربط outcomes بـ knowledge capture
            if KNOWLEDGE_CAPTURE_AVAILABLE:
                try:
                    knowledge_capture.update_outcomes_from_paper_trading()
                except Exception as e:
                    print(f"  ⚠️ فشل ربط outcomes: {e}")
            
            db = paper_trading_engine.load_trades()
            print(f"  📈 ملخص: نشطة={len(db['active'])} | مغلقة={len(db['closed'])}")
        except Exception as e:
            print(f"  ⚠️ فشل paper trading: {e}")
            traceback.print_exc()
    else:
        if not PAPER_TRADING_AVAILABLE:
            print("  ⚠️ Paper Trading modules غير متاحة")
        else:
            print("  ⚠️ لا توجد candidates للـ paper trading")

    # ════════════════════════════════════════════════
    # [6/7] التقرير HTML
    # ════════════════════════════════════════════════
    print("\n[6/7] 📄 بناء التقرير HTML")
    print("─" * 60)
    try:
        build_reports_v9.run()
    except Exception as e:
        print(f"  ❌ فشل التقرير: {e}")
        traceback.print_exc()

    # ════════════════════════════════════════════════
    # [7/7] Excel Dashboard + الأرشفة + Knowledge Stats
    # ════════════════════════════════════════════════
    print("\n[7/7] 💾 Excel Dashboard + أرشفة الأوزان + إحصاءات المعرفة")
    print("─" * 60)
    
    # Excel dashboard للـ paper trading
    if PAPER_TRADING_AVAILABLE:
        try:
            paper_trading_excel.build_dashboard()
        except Exception as e:
            print(f"  ⚠️ فشل بناء Excel dashboard: {e}")
    
    # عرض إحصاءات قاعدة المعرفة
    if KNOWLEDGE_CAPTURE_AVAILABLE:
        try:
            stats = knowledge_capture.update_knowledge_stats()
            if stats:
                total = stats.get("total_decisions", 0)
                maturity = stats.get("maturity", "?")
                readiness = stats.get("readiness_for_distillation", "?")
                print(f"  🧠 قاعدة المعرفة: {total} قرار محفوظ | النضج: {maturity} | جاهزية الاستقلالية: {readiness}")
        except Exception as e:
            print(f"  ⚠️ فشل تحديث knowledge stats: {e}")
    
    try:
        archive_weights()
        archive_ml_metrics()
        print("  ✓ تمت الأرشفة")
    except Exception as e:
        print(f"  ⚠️ فشل الأرشفة: {e}")

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n{'═'*60}")
    print(f"  ✅ اكتمل في {elapsed:.1f} ثانية")
    print(f"{'═'*60}\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
