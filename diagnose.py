# -*- coding: utf-8 -*-
"""
سكريبت تشخيصي — V9
يختبر كل مكوّن على حدة ليحدد أين تكمن المشكلة بالضبط.
شغّله عبر: python diagnose.py
"""
import sys
import traceback
from pathlib import Path

print("═" * 60)
print("  🔍 تشخيص بيئة V9")
print("═" * 60)

# 1) Python version
print(f"\n✓ Python: {sys.version}")

# 2) المكتبات
print("\n📦 المكتبات:")
libs = {
    "yfinance": None, "pandas": None, "numpy": None,
    "anthropic": None, "xgboost": None, "sklearn": None,
}
for lib in libs:
    try:
        mod = __import__(lib)
        libs[lib] = getattr(mod, "__version__", "unknown")
        print(f"  ✓ {lib}: {libs[lib]}")
    except ImportError as e:
        print(f"  ❌ {lib}: {e}")

# 3) مفاتيح البيئة
import os
print("\n🔑 المتغيرات البيئية:")
api_key = os.environ.get("ANTHROPIC_API_KEY", "")
if api_key:
    print(f"  ✓ ANTHROPIC_API_KEY: موجود ({len(api_key)} حرف، يبدأ بـ {api_key[:8]}...)")
else:
    print("  ❌ ANTHROPIC_API_KEY: غير موجود")

eod_key = os.environ.get("EODHD_API_KEY", "")
print(f"  {'✓' if eod_key else 'ℹ️'} EODHD_API_KEY: {'موجود' if eod_key else 'غير موجود (yfinance سيُستخدم)'}")

# 4) المجلدات
print("\n📁 المجلدات:")
for d in ["tadawul_data", "ml_models", "public", "weights_history"]:
    p = Path(d)
    status = "موجود" if p.exists() else "سيُنشأ"
    try:
        p.mkdir(parents=True, exist_ok=True)
        print(f"  ✓ {d}: {status}")
    except Exception as e:
        print(f"  ❌ {d}: {e}")

# 5) استيراد ملفات المشروع
print("\n🧩 استيراد وحدات V9:")
modules = ["data_sources", "indicators", "correlation_engine",
           "ml_engine", "scanner_v9", "ai_analyst_v9", "build_reports_v9"]
for m in modules:
    try:
        __import__(m)
        print(f"  ✓ {m}")
    except Exception as e:
        print(f"  ❌ {m}: {e}")
        traceback.print_exc()

# 6) اختبار جلب بيانات
print("\n📡 اختبار جلب بيانات (yfinance):")
try:
    import yfinance as yf
    import pandas as pd

    # اختبار سهم سعودي
    print("  → سهم أرامكو (2222.SR)...")
    df = yf.download("2222.SR", period="10d", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if not df.empty and "Close" in df.columns:
        print(f"    ✓ {len(df)} شمعة، آخر إغلاق: {float(df['Close'].iloc[-1]):.2f}")
    else:
        print("    ❌ البيانات فارغة")

    # اختبار ماكرو
    print("  → النفط (CL=F)...")
    df = yf.download("CL=F", period="5d", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if not df.empty:
        print(f"    ✓ آخر سعر: {float(df['Close'].iloc[-1]):.2f}")
    else:
        print("    ❌ فارغ")

    # اختبار TASI
    print("  → مؤشر TASI (^TASI.SR)...")
    try:
        df = yf.download("^TASI.SR", period="5d", progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if not df.empty:
            print(f"    ✓ آخر قيمة: {float(df['Close'].iloc[-1]):.2f}")
        else:
            print("    ⚠️ فارغ (طبيعي، سيُعطَّل RS)")
    except Exception as e:
        print(f"    ⚠️ {e}")

except Exception as e:
    print(f"  ❌ فشل اختبار yfinance: {e}")
    traceback.print_exc()

# 7) اختبار الاتصال بـ Anthropic (بدون إرسال رسالة)
if api_key:
    print("\n🧠 اختبار مفتاح Anthropic:")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say OK"}]
        )
        print(f"  ✓ المفتاح يعمل. الرد: {msg.content[0].text[:50]}")
        print(f"  ✓ tokens: {msg.usage.input_tokens}→{msg.usage.output_tokens}")
    except Exception as e:
        print(f"  ❌ {e}")

print("\n" + "═" * 60)
print("  انتهى التشخيص")
print("═" * 60)
