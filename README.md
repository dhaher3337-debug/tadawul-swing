# 🇸🇦 ماسح تداول الذكي — V9

نظام آلي متكامل لتحليل السوق السعودي (تداول) يومياً بـ 18 مؤشر فني + تعلم آلي حقيقي (XGBoost) + Claude Opus 4.7 + تحليل ارتباطات وتدفق سيولة قطاعي.

---

## 🆕 الجديد في V9 (مقارنة بـ V8)

### 🔴 إصلاحات حرجة
- **fetch_macro يعمل الآن**: كانت كل بيانات النفط/S&P/VIX/الذهب ترجع "N/A" في V8 بسبب تغيير yfinance (Series بدلاً من scalar). الآن Claude Opus يحصل على بيانات ماكرو حقيقية.
- **تقييم الأمس لا يقارن اليوم بنفسه**: كان V8 يُعطي `0/0 (0%)` لأن الكود يُلحق توقعات اليوم في history ثم يقيّم نفسه. الآن يأخذ آخر entry تاريخه قبل اليوم فقط.
- **تعريف hit واقعي**: كان V8 يعتبر أي ارتفاع 0.01% "نجاح". V9 يعتبر hit = +1.5% خلال 3 أيام **دون** اختراق وقف -2% أولاً.
- **VWAP حقيقي**: كان V8 يحسب moving average ويسميه VWAP. V9 يحسب Anchored VWAP من قاع آخر 20 يوم.
- **OBV vectorized**: أسرع بـ ~50× من حلقة V8.
- **لا تكرار في SECTOR_MAP**: كان الرمز 4050 مكرراً في "نقل" و"تجزئة" في V8. حُذف من "نقل".
- **SMA50 يُحسب صحيحاً**: V8 كان يجلب `period="120d"` فقط → نصف البيانات NaN. V9 يجلب 200 يوم.

### ⭐ مؤشرات جديدة (7 إضافية)
- **ADX + DI+/DI-**: قياس قوة الاتجاه. ADX>25 = اتجاه قوي، <20 = سوق عرضي (لا تتداول breakouts).
- **Supertrend**: اتجاه ديناميكي. تبديله من هابط إلى صاعد = إشارة قوية.
- **Ichimoku Cloud**: 5 مكونات (Tenkan, Kijun, Senkou A/B, Chikou). السعر فوق السحابة + Tenkan>Kijun = صاعد قوي.
- **MFI (Money Flow Index)**: RSI معدّل بالحجم. MFI<25 = تشبع بيع مؤكد بضعف سيولة.
- **Fibonacci Retracement**: مواقع 38.2% و 61.8% تلقائياً من قمة/قاع 20-يوم.
- **CMF (Chaikin Money Flow)**: CMF>0.1 = تراكم مؤسسي.
- **Relative Strength vs TASI**: مقارنة أداء السهم بالمؤشر الكلي (20 يوم).

### 🤖 تعلم آلي حقيقي (XGBoost)
- 19 feature لكل سهم
- تدريب يومي على آخر ~90 يوم من البيانات الحقيقية
- يُرجع probability (0-1) لكل مرشح — يُدمج في scoring
- Feature importance من النموذج يُوجّه تعديل الأوزان (بديل أذكى من مضاعفات 1.02/0.97 في V8)
- Metrics يومية: Accuracy, ROC-AUC, Precision, Recall, F1

### 🔗 تحليل الارتباطات والسيولة (طلبك الصريح)
- **Correlation Matrix** لكل الأسهم (30 يوم)
- **فرص Catch-up**: إذا A ارتفع +3% و B (المرتبط تاريخياً بـ A) لم يتحرك، B قد يلحق
- **تدفق السيولة القطاعي**: صافي volume × price × direction لكل قطاع
- **Leader/Laggard** داخل كل قطاع: من قاد اليوم، من تأخر
- **Sector Rotation**: اكتشاف تبادل الأدوار (قطاع كان ضعيفاً → قوي)

### 📊 تحسينات Scoring
- **Expected Value** لكل مرشح: `ML_prob × target_gain - (1-prob) × stop_loss`
- **Risk/Reward ratio** — يُستبعد <1.5 تلقائياً
- **Confluence bonus**: إجماع ≥4 إشارات = +0.6 لكل إشارة إضافية
- **Weekly trend filter**: صاعد ×1.25، هابط ×0.75
- **ADX filter**: ADX<15 يُخفض النقاط (لا تتداول في سوق عرضي)

### 💰 تكلفة Claude أقل (Opus 4.7)
- V8 كان يحسب $15/$75 لكل مليون token
- Opus 4.7 الفعلي: **$5/M input, $25/M output** — أرخص بـ3×
- التكلفة المتوقعة اليومية: $0.02-$0.05 (السنوية: ~$10-20)

---

## 📦 التثبيت والتشغيل

### 1) استنساخ المشروع
```bash
git clone <your-repo>
cd tadawul-v9
pip install -r requirements.txt
```

### 2) إعداد المفاتيح (GitHub Secrets)
في `Settings → Secrets and variables → Actions`:

- `ANTHROPIC_API_KEY` (إجباري): من [console.anthropic.com](https://console.anthropic.com)
- `EODHD_API_KEY` (اختياري): من [eodhd.com](https://eodhd.com) للبيانات المدفوعة ($19.99/شهر)

**ملاحظة حول البيانات المدفوعة**: النظام يعمل افتراضياً بـ yfinance (مجاني). إذا أضفت `EODHD_API_KEY` فسيستخدم EOD تلقائياً لبيانات أدق وأسرع. لا تعديل كود مطلوب.

### 3) تفعيل GitHub Pages
`Settings → Pages → Source: GitHub Actions`

### 4) التشغيل اليدوي الأول
`Actions → Daily Analysis V9 → Run workflow`

---

## 🕐 الجدولة

- يعمل تلقائياً الأحد-الخميس الساعة 5 صباحاً سعودي
- التقرير ينشر على `https://<username>.github.io/<repo>/`
- يمكن التشغيل اليدوي من تبويب Actions

---

## 📁 هيكل الملفات

```
tadawul-v9/
├── data_sources.py          # جلب بيانات موحد (yfinance + EODHD اختياري)
├── indicators.py            # 18 مؤشر vectorized
├── correlation_engine.py    # ارتباطات + دوران قطاعي + تدفق سيولة
├── ml_engine.py             # XGBoost + feature importance
├── scanner_v9.py            # الماسح الرئيسي
├── ai_analyst_v9.py         # Claude Opus 4.7 analyst
├── build_reports_v9.py      # HTML تقرير
├── backtest.py              # محرك backtest (منفصل)
├── run_all.py               # المنسق الرئيسي
├── requirements.txt
├── .github/workflows/
│   └── daily_analysis.yml   # GitHub Actions
├── tadawul_data/            # بيانات ونتائج (تُحدّث يومياً)
│   ├── tasi_candidates.json
│   ├── tasi_weights.json
│   ├── tasi_history.json
│   ├── tasi_tracker.json
│   ├── ml_metrics.json
│   ├── feature_importance.json
│   ├── ml_dataset.csv
│   ├── ai_result.json
│   └── ai_learning_log.json
├── weights_history/          # أرشيف يومي للأوزان ومقاييس ML
├── ml_models/                # نموذج XGBoost المدرّب
│   └── xgb_model.pkl
└── public/                   # التقرير الناشر
    └── index.html
```

---

## 🧪 Backtest (قياس الأداء التاريخي)

قبل الاعتماد على النظام:
```bash
python backtest.py --days 60 --top 10
```

يعطيك:
- نسبة النجاح الحقيقية (hit rate)
- متوسط الربح/الخسارة لكل صفقة
- Sharpe ratio ومعدل السحب الأقصى
- أداء كل إشارة منفردة (أيها فعلاً يربح)

**يُنصح بتشغيله شهرياً** لمتابعة تحسن النظام.

---

## 🎯 استراتيجية التداول المُطبقة

- **مدة الاحتفاظ القصوى**: 5 أيام swing
- **وقف الخسارة**: max(close − 2×ATR, Supertrend)
- **هدف 1**: close + 2×ATR (≈ +2.5-4%)
- **هدف 2**: close + 3.5×ATR (≈ +5-7%)
- **R:R الأدنى المقبول**: 1:1.5
- **لا تداول في السوق العرضي** (ADX<15)
- **فلترة الأسبوعي**: أفضلية للاتجاه الصاعد
- **تركيز قصوى لقطاع واحد**: 30% (تحذير Opus)

---

## 🔬 ما يتعلّمه النظام يومياً

1. **أداء كل إشارة الحقيقي** (hit rate واقعي): إذا RSI<35 يعطي 60% نجاح، وزنه يرتفع. إذا Stoch RSI يعطي 30%، وزنه ينخفض.
2. **Feature importance من XGBoost**: النموذج يكتشف بنفسه أي مميزة أهم (مثلاً: قد يكتشف أن CMF أقوى من volume_ratio لأسهم معينة).
3. **Claude Opus insights**: يُلاحظ pattern لم تلتقطه المؤشرات، ويقترح تعديلات.
4. **Dynamic blacklist**: سهم يفشل 7 مرات بأداء <25% → blacklist 60 يوم، ثم فرصة ثانية.

---

## ⚠️ تحذيرات

- **ليس نصيحة مالية** — أداة مساعدة بحثية فقط
- التداول ينطوي على خسائر محتملة للمال
- Backtest لا يضمن الأداء المستقبلي
- تأكد من تقييم كل فرصة بحدسك وخبرتك قبل التنفيذ
- **لا تعتمد على النظام قبل تشغيل backtest.py لمدة 60 يوم على الأقل**

---

## 🛠 استكشاف الأخطاء

- **"ML model not trained"**: النظام يحتاج ~100 عينة قبل التدريب. بعد ~5 أيام تشغيل يبدأ التعلم.
- **"N/A" في الماكرو**: تحقق من الاتصال. الكاش يُخزن 10 دقائق.
- **لا توجد فرص catch-up**: طبيعي في الأسواق المستقرة. تظهر عند تحركات قوية متفرقة.
- **Opus error**: تحقق من رصيد ANTHROPIC_API_KEY.

---

**النسخة**: 9.0.0
**التاريخ**: 20 أبريل 2026
**Claude Model**: claude-opus-4-7
