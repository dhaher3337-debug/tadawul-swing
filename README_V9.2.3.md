# 🚀 Tadawul Swing V9.2.3 — Reality-Based Tightening
**التاريخ:** 17 مايو 2026
**الحالة:** ملفات جاهزة للنسخ المباشر
**الفلسفة:** "البيانات قالت، والكود استمع"

---

## 📊 السياق

بعد أسبوع من V9.2.2 (1-15 مايو 2026)، البيانات الحقيقية أظهرت:

| المقياس | الواقع | الحُكم |
|---|---|---|
| Win Rate | 36% (9W/16L) | تحت العشوائية |
| Profit Factor | 0.55 | كارثي (يجب >1.3) |
| Expectancy | -1.04% / صفقة | خاسر إحصائياً |
| Total PnL (8 أيام) | **-26.09%** | غير مقبول |

### المحاكاة على نفس البيانات بقواعد V9.2.3:

| المقياس | V9.2.2 (الواقع) | V9.2.3 (محاكاة) |
|---|---|---|
| الصفقات | 25 | **5** |
| Win Rate | 36% | **80%** |
| PnL | -26.09% | **+14.71%** |
| PF | 0.55 | **7.34** |

**الفرق: من −26% إلى +15% بدون أي feature جديدة، فقط بتشديد الفلاتر.**

---

## 🐛 الأخطاء التي تم إصلاحها

### Bug 1 (P0) — تكرار فتح الصفقات (5 صفقات مكررة في 12/5)
```
T0021 + T0023 + T0026 على 2280 (نفس اليوم نفس entry/stop)
T0020 + T0024 على 2370 (نفس اليوم)
```
**الخسارة المباشرة:** −14.97% من 4 تكرارات.

**الإصلاح:** 3 طبقات حماية في `paper_trading_engine.py`:
1. `new_tickers_this_call` set يمنع التكرار في نفس الاستدعاء
2. `opened_today` يستثني الأسهم المفتوحة سابقاً في نفس اليوم
3. `_has_run_today("open")` يمنع double-call للدالة

### Bug 2 (P0) — ML overfit (AUC=0.937 لكن WR=36%!)
```
samples=242 ثابتة 6 أيام (لا retrain فعلي)
precision=1.0 في in-sample
ML[0.4–0.6): WR_actual=0% (عكس التوقع تماماً)
```
**الإصلاح في `ml_engine.py`:**
1. Walk-forward CV (3-fold) بدل static 80/20
2. `random_state=None` في النموذج النهائي (تباين حقيقي)
3. كشف overfit gap (in-sample - out-of-sample AUC)
4. `ml_trustability.json` - يُعطّل ml_probability إذا AUC < 0.55
5. `predict_probability(respect_trustability=True)` يحترم التقييم

### Bug 3 (P0) — Targets ثابتة 5%/8% (بعيدة جداً)
```
1 من 25 صفقة فقط ضربت T2 (4%)
16 من 25 خرجت بـ Max Holding (64%)
"Leaked gains": +1.1% إلى +2.7% لكل صفقة رابحة
```
**الإصلاح في `paper_trading_engine.py`:**
1. ATR-based: stop=−1.5×ATR, T1=+1.5×ATR, T2=+3×ATR
2. حدود سلامة: stop بين −1.5% و −8%, T1 ≥ +1.8%
3. R:R الأدنى المضمون: 1:2 لـ T2

### Bug 4 (P0) — `days_held` غير مُسجَّل
**الإصلاح:** يُحسب من `(close_date - open_date).days` فور الإغلاق.

### Bug 5 (P1) — Stop ثابت بعد T1 (Breakeven)
**سابقاً:** بعد T1، stop ينتقل لـ breakeven فقط ويبقى ثابتاً.
**الإصلاح:** Trailing stop ATR-based = `max_high_seen - 1.5×ATR`، يصعد فقط.

---

## 🎯 التغييرات الجوهرية في القواعد

### `rules_filter.py`:

| القاعدة | V9.2.2 | V9.2.3 | السبب |
|---|---|---|---|
| `min_score` | **4.0** | **18.0** | البيانات: score≥20 = WR 83%, score<18 = WR 21% |
| `min_active_signals` | 3 | 5 | إجماع أقوى |
| `min_adx` | 15 | 20 | تجنب الأسواق العرضية |
| `min_ev_pct` | 0.0 | 0.5 | استبعاد EV ضعيف |
| `min_volume_ratio` | غير موجود | 0.8 | تجنب الأسهم الميتة |
| `max_rsi_for_entry` | غير موجود | **72.0** | منع late breakouts |
| `max_mfi_for_entry` | غير موجود | 85.0 | منع overbought |
| `blocked_signal_types` | لا شيء | **[default, mean_reversion]** | WR=0% للنوعين |
| `block_negative_sectors` | لا شيء | **flow < -500M** | منع قطاعات منهارة |
| `top_n` (picks) | 7 | **5** | انتقائية أعلى |

### `paper_trading_engine.py`:

| الإعداد | V9.2.2 | V9.2.3 |
|---|---|---|
| Stop calculation | ثابت (% من السعر) | **ATR-based** (1.5×ATR) |
| Target1 | ثابت (~5%) | **ATR-based** (1.5×ATR) |
| Target2 | ثابت (~8%) | **ATR-based** (3.0×ATR) |
| Trailing بعد T1 | Breakeven ثابت | **ATR Trailing** (يصعد فقط) |
| Default max holding | 7 أيام | 6 أيام |
| `days_held` | غير مُسجَّل | ✅ مُسجَّل |
| Duplicate protection | ❌ | ✅ 3 طبقات |
| ML dataset feeding | ❌ معطّل | ✅ كل صفقة مغلقة تُكتب |

---

## 📦 الملفات المُسلَّمة

| الملف | الحالة | الحجم | الإجراء |
|---|---|---|---|
| `paper_trading_engine.py` | ✏️ **استبدال** | ~22 KB | انسخه فوق الموجود |
| `rules_filter.py` | ✏️ **استبدال** | ~18 KB | انسخه فوق الموجود |
| `ml_engine.py` | ✏️ **استبدال** | ~13 KB | انسخه فوق الموجود |
| `rebuild_ml_dataset.py` | 🆕 جديد | ~6 KB | شغّله **مرة واحدة** |
| `README_V9.2.3.md` | 🆕 جديد | هذا الملف | احفظه للمرجع |

**ملفات لم تتغيّر** (تبقى كما هي):
- `scanner_v9.py` (يكتب ATR بالفعل في candidate)
- `power_classifier.py` (يعمل كما هو)
- `ai_analyst_v9.py` (لا تأثير عليه)
- `news_engine.py`, `earnings_calendar.py`, `correlation_engine.py`, etc.

---

## 🚀 خطوات التطبيق

### 1. Backup أولاً (إجباري!)
```bash
cd ~/tadawul-swing
mkdir -p _backup_v922_$(date +%Y%m%d)
cp paper_trading_engine.py rules_filter.py ml_engine.py _backup_v922_*/
cp tadawul_data/paper_trades.json tadawul_data/ml_dataset.csv _backup_v922_*/ 2>/dev/null
git add . && git commit -m "Backup before V9.2.3"
```

### 2. استبدال الملفات الـ 3
```bash
# انسخ من /home/claude/output/ في Claude إلى مشروعك
cp paper_trading_engine.py rules_filter.py ml_engine.py ~/tadawul-swing/
```

### 3. شغّل rebuild_ml_dataset (مرة واحدة فقط!)
```bash
cd ~/tadawul-swing
python3 rebuild_ml_dataset.py --keep-old
```

**النتيجة المتوقعة:**
- يأخذ نسخة احتياطية من `ml_dataset.csv` القديم
- يبني dataset جديد من 25 صفقة paper trading حقيقية
- يُشغّل التدريب الجديد بـ walk-forward CV
- يُنتج `ml_trustability.json` مع تقييم النموذج

**ملاحظة:** بعد 25 صفقة فقط، النموذج سيُصنَّف "weak" أو "unreliable" - وهذا **صحيح**. بدلاً من overfit مزيّف.

### 4. تشغيل دورة عادية
```bash
python3 run_all.py
```

تحقق من:
- `tadawul_data/ai_result.json` يحتوي `version: "V9.2.3"`
- `tadawul_data/ml_trustability.json` موجود وفيه `trust_level`
- عدد picks ≤ 5
- معظم picks بـ `score ≥ 18`

### 5. commit النتائج
```bash
git add tadawul_data/ paper_trades/ ml_models/
git commit -m "V9.2.3: tightened filters + ATR-based exits + WF-CV ML"
git push
```

---

## ⚠️ تحذيرات مهمة

### 1. **سيقلّ عدد الصفقات اليومية كثيراً**
- من ~5 صفقات/يوم → ربما 0-2 صفقات/يوم
- هذا **مطلوب** - انتقائية أعلى = نتائج أفضل
- لا تخفّض `min_score` تحت 18 لأي سبب!

### 2. **ML probability سيختفي من picks في البداية**
- لأن النموذج بعد rebuild سيُصنَّف "weak"
- `predict_probability()` ترجع `None` احتراماً للموثوقية
- بعد 200-300 صفقة، النموذج سيصبح "trusted" ويعمل
- في الفترة الانتقالية: rules_filter يكفي

### 3. **بعض الإشارات الجديدة قد تُفلتر خطأً**
- إذا فلتر sector_flow حجب سهماً واضحاً جيداً، راجع `sector_flows_prev.json`
- إذا اعترضت على رفض معيّن، شاهد `rejected_candidates` في `ai_result.json`

### 4. **شغّل `rebuild_ml_dataset.py` مرة واحدة فقط**
- لو شغلته مرتين، ستُلغى البيانات السابقة
- الـ rebuild يأخذ نسخة احتياطية تلقائياً

---

## 📊 معايير النجاح للأسبوع القادم (24 مايو)

نُقيّم V9.2.3 بـ:

| المقياس | الهدف الأدنى | الهدف المرجو |
|---|---|---|
| Win Rate | ≥ 50% | ≥ 65% |
| Profit Factor | ≥ 1.2 | ≥ 1.8 |
| Total PnL (أسبوع) | > 0% | > +5% |
| لا duplicates | إجباري | ✓ |
| لا signal_type=default | إجباري | ✓ |
| ATR-based exits in picks | ≥ 80% | 100% |

**إذا لم يتحقق "الهدف الأدنى"**، نُراجع المعايير - لا نُضيف features جديدة.

---

## 🔬 ما تم تأجيله إلى V9.3 (بعد إثبات V9.2.3)

- ❌ Inter-Stock Correlation network
- ❌ Stock DNA Profile
- ❌ Lead-Lag detection
- ❌ Volume Profile per stock
- ❌ Sector rotation engine
- ❌ News sentiment 2.0

**الفلسفة:** لا نُضيف feature جديد حتى يثبت V9.2.3 ربحيته 4 أسابيع متتالية.

---

## 📈 خطة الأسبوع

| اليوم | المهمة |
|---|---|
| الأحد 17/5 | استبدال الملفات + rebuild ML |
| الإثنين 18/5 | أول جلسة V9.2.3 - راقب logs بعناية |
| الإثنين-الخميس | جمع 4 جلسات بيانات حقيقية |
| الخميس 21/5 | فحص أولي (إذا 4+ صفقات مغلقة) |
| الأحد 24/5 | تحليل كامل أسبوع V9.2.3 → قرار V9.2.4 أو الانتقال لـ V9.3 |

---

## 🤝 ملاحظات أخيرة

1. **هذا ليس feature update** - هذا **bug-fix + tightening**.
2. **الجودة قبل الكمية**: قبول 1 صفقة Win بـ +5% أفضل من 5 صفقات WR=20%.
3. **شفافية كاملة**: كل قرار رفض موثّق في `rejected_candidates` لمراجعته.
4. **العودة سهلة**: إذا V9.2.3 لا يعمل، نعود لـ V9.2.2 من backup ونحلل لماذا.

---

**النسخة:** V9.2.3
**التاريخ:** 17 مايو 2026
**المؤلف:** Claude + Dhaher
**الحالة:** جاهزة للنشر
