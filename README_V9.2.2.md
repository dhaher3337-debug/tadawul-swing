# 🚀 Tadawul Swing V9.2.2 — Power Classifier Integration

**حزمة الدمج النهائية مبنية على الكود الفعلي لـ V9.2**

تم بناء هذه الحزمة بعد قراءة الكود الكامل لمشروعك (`tadawul-swing-main.zip`)
وهي **ليست patches**، بل **الملفات المعدّلة كاملة وجاهزة للنسخ المباشر**.

---

## 📦 الملفات المُسلَّمة (5 ملفات)

| الملف | الحالة | الإجراء |
|------|--------|---------|
| `power_classifier.py` | 🆕 جديد | انسخه لمجلد المشروع |
| `scanner_v9.py` | ✏️ معدّل | استبدل الموجود |
| `rules_filter.py` | ✏️ معدّل | استبدل الموجود |
| `ai_analyst_v9.py` | ✏️ معدّل | استبدل الموجود |
| `gemini_v301_ultra.pine` | 🆕 جديد | استورده في TradingView |

---

## 🎯 ملخص التغييرات في كل ملف

### 1. `power_classifier.py` (جديد - 525 سطر)
الموديول الكامل لحساب Power Score بـ 7 فلاتر:
- Volume Spike (25 نقطة)
- Trend Alignment (20)
- Candle Strength (15)
- RSI Sweet Spot (15)
- ATR Expansion (10)
- Close Position (10)
- Bollinger Squeeze (5)

**API الرئيسي:** `enrich_candidate_with_power(candidate, df)`

### 2. `scanner_v9.py` (تعديلان فقط - 25 سطر مُضاف)

**التغيير 1** - السطر ~71 (إضافة import):
```python
try:
    from power_classifier import enrich_candidate_with_power
    POWER_AVAILABLE = True
except ImportError:
    POWER_AVAILABLE = False
    def enrich_candidate_with_power(candidate, df):
        return candidate
```

**التغيير 2** - السطر ~1045 (تعديل candidates.append):
- بدل `candidates.append({...})` صار `candidate_dict = {...}` ثم enrichment ثم append
- يُمرَّر `df` (الذي يحوي OHLCV + كل المؤشرات) للـ enricher
- إذا ROCKET/STRONG، يُضاف `power_breakout` لـ `signals`

### 3. `rules_filter.py` (3 تعديلات)

**التغيير 1** - `SCORE_WEIGHTS` (السطر ~80):
```python
SCORE_WEIGHTS = {
    "score": 0.25,           # كان 0.30
    "ev_pct": 0.25,          # كان 0.30
    "risk_reward": 0.13,     # كان 0.15
    "adx": 0.08,             # كان 0.10
    "mtf_alignment": 0.09,   # كان 0.10
    "volume_signal": 0.05,
    "power_score": 0.15,     # 🆕 جديد
}
```

**التغيير 2** - `compute_composite_score` (السطر ~250):
أضفنا مكوّن `power_score` المُطبَّع: power=100→1.0, power=50→0.0

**التغيير 3** - بناء `reason` (السطر ~337):
إبراز ROCKET/STRONG في بداية reason مع emoji.
**النتيجة في التقرير**: `🚀🚀🚀 ROCKET (87/100) - إجماع 6 إشارات: RSI + MACD + ...`

### 4. `ai_analyst_v9.py` (4 تعديلات)

**التغيير 1** - `_format_candidates` (السطر ~44):
إضافة عمود Power info لكل candidate في الـ prompt (يرى Opus التصنيف فوراً)

**التغيير 2** - دالة جديدة `_format_power_summary` (السطر ~65):
ملخص ROCKET/STRONG/CRASH/DUMP في قسم مستقل

**التغيير 3** - System Prompt (السطر ~310):
- إضافة Power Classification لمعايير التفضيل الإيجابية
- قسم كامل يشرح Power Classifier لـ Opus + كيف يستخدمه

**التغيير 4** - JSON Schema (السطر ~349):
حقل جديد في picks: `power_assessment` يجبر Opus على تقييم قوة الكسر

### 5. `build_reports_v9.py` (لا يحتاج تعديل ✅)
لأن:
- `reason` في rules_filter يبدأ بالـ emoji الذهبي/الأخضر
- HTML report يعرض `reason` تلقائياً كما هو
- إيموجي ROCKET يظهر بصرياً بدون أي كود إضافي

---

## 📋 خطوات التطبيق (15 دقيقة)

### الخطوة 1: نسخ الملفات للمشروع
```bash
cd /path/to/tadawul-swing
cp /downloads/power_classifier.py ./
cp /downloads/scanner_v9.py ./       # ⚠️ سيستبدل الموجود
cp /downloads/rules_filter.py ./     # ⚠️ سيستبدل الموجود
cp /downloads/ai_analyst_v9.py ./    # ⚠️ سيستبدل الموجود
```

### الخطوة 2: اختبار power_classifier مستقلاً
```bash
python power_classifier.py
```
يجب أن ترى تحليلاً لـ 3 أسهم تجريبية (يحتاج yfinance).

### الخطوة 3: اختبار scanner_v9
```bash
python scanner_v9.py
```
بعد الانتهاء، افتح `tadawul_data/tasi_candidates.json` وتأكد من وجود:
```json
{
  "ticker": "2222",
  "score": 7.5,
  "power_score": 75,
  "power_classification": "STRONG",
  "power_emoji": "🚀🚀",
  "power_breakout_level": 27.40,
  "power_targets": {"T1_up": 28.50, ...}
}
```

### الخطوة 4: اختبار rules_filter
```bash
# للاختبار بدون API key (rules_filter mode)
unset ANTHROPIC_API_KEY
python rules_filter.py
```
في `ai_result.json`، تأكد من picks تحوي reasons تبدأ بـ `🚀🚀 STRONG (...) - ...`

### الخطوة 5: اختبار كامل مع Opus
```bash
export ANTHROPIC_API_KEY="..."
python run_all.py
```
تأكد من:
- ✅ Opus يذكر "Power" في `market_comment`
- ✅ كل pick يحوي حقل `power_assessment`
- ✅ التقرير HTML يعرض الإيموجيات بصرياً

### الخطوة 6: Commit & Push
```bash
git add power_classifier.py scanner_v9.py rules_filter.py ai_analyst_v9.py
git commit -m "feat(v9.2.2): integrate Power Classifier (Gemini V301)

- Add power_classifier.py module (7-filter scoring system)
- scanner_v9: enrich candidates with power_score
- rules_filter: add power_score (15%) to composite (now 7 components)
- ai_analyst_v9: pass power signals to Opus + new power_assessment field
- HTML reports: power emojis show automatically via reason"
git push origin main
```

---

## ⚠️ نقاط حرجة يجب الانتباه لها

### 1. **POWER_AVAILABLE = False fallback**
إذا فشل import (مثلاً نسيت نسخ `power_classifier.py`)، النظام **لا يتعطل**.
يستخدم `enrich_candidate_with_power = no-op`، وكل candidates يحصلون على `power_score=0`.

### 2. **`df` في scanner**
في السطر الذي عدّلناه، `df` هو نفس الـ DataFrame بعد `compute_all()`.
هذا يحوي **OHLCV الأصلي + كل المؤشرات المحسوبة**.
`power_classifier` يعيد حساب EMA/RSI/ATR من OHLCV (لا يعتمد على المؤشرات المحسوبة في scanner).
هذا **مقصود** لضمان تطابق منطقي مع Pine Script V301.

### 3. **حد power_score = 50**
في `compute_composite_score`:
```python
if power_score >= 50:
    components["power_score"] = (power_score - 50) / 50.0
else:
    components["power_score"] = 0.0
```
لماذا 50؟ لأن < 50 يعني NONE/WEAK، لا يستحق تحفيز composite.

### 4. **'power_breakout' في signals**
عندما ROCKET/STRONG يحدث، نضيف `power_breakout` للـ `signals` list.
**التأثير**:
- يُحسب في `len(signals)` الذي يستخدمه rules_filter (min_active_signals=3)
- يظهر في `reasons` التي تُعرض في التقرير
- قد يؤثر على confluence_boost في scanner_v9 (السطر 1040)

### 5. **لا قاعدة إقصاء جديدة**
لم أضف قاعدة "إذا power_score < X رفض" لأنك تحتاج **قياس أداءه أولاً**.
بعد أسبوعين من البيانات، يمكنك إضافة:
```python
RULES["min_power_for_strong_buy"] = 65  # للـ "شراء قوي" فقط
```

---

## 🔬 خطة القياس الإجبارية (3 أسابيع)

كما تعرف من تجربتك مع ML (AUC=0.48 → معطّل)، **لا تثق بأي إشارة بدون قياس**.

### الأسبوع 1-2: جمع البيانات (بدون تعديل)
- شغّل النظام يومياً
- لا تعدّل أي وزن
- اجمع بيانات كل ROCKET و STRONG

### الأسبوع 3: التحليل
لكل ROCKET ظهر في الأسبوعين:
- هل حقق T1؟ T2؟ T3؟
- متى؟ (3 أيام؟ 7 أيام؟)
- هل ضرب stop أولاً؟

النتائج المتوقعة (تقريباً، للسوق السعودي):
- ROCKET Win Rate (T1): **60-75%** (إذا أقل من 50% → خطأ في الفلاتر)
- STRONG Win Rate (T1): **50-65%**
- متوسط T1 hit time: **3-7 أيام**

### الأسبوع 4: المعايرة
- إذا ROCKET Win Rate > 70% → ارفع وزنه (15% → 20%)
- إذا STRONG Win Rate < 50% → خفّض الحد الأدنى (65 → 70)

---

## 🎁 ميزات المستقبل (V9.3)

بعد القياس الناجح، يمكن:

### A. **`build_reports_v9.py`**: قسم خاص للـ ROCKETs
أضف بعد قسم picks:
```python
rocket_picks = [p for p in result['picks'] if p.get('power_classification') == 'ROCKET']
if rocket_picks:
    html += '<section class="rockets">'
    html += '<h2>🚀🚀🚀 أعلى فرص الكسر اليوم</h2>'
    for p in rocket_picks:
        targets = p.get('power_targets', {})
        html += f'<div class="rocket-card">'
        html += f'<h3>{p["ticker"]} - {p.get("sector")}</h3>'
        html += f'<p>Power: {p["power_score"]}/100</p>'
        html += f'<p>T1: {targets.get("T1_up")} | T2: {targets.get("T2_up")} | T3: {targets.get("T3_up")}</p>'
        html += '</div>'
    html += '</section>'
```

### B. **Pre-Breakout Detection**
أسهم بـ `power_score=45-49` لعدة أيام → watchlist للانفجار القريب.

### C. **TradingView Webhook**
ربط V301 Pine Script بـ GitHub Actions لإرسال تنبيهات لحظية.

### D. **Power Backtest**
بناء `power_backtest.py` لقياس Win Rate على بيانات سنوات.

---

## 📊 مثال JSON النهائي بعد الدمج

```json
{
  "date": "2026-05-15",
  "model": "claude-opus-4-7",
  "market_outlook": "صاعد",
  "market_comment": "السوق صاعد بقيادة قطاع الطاقة. اليوم رأينا 2 ROCKETs (أرامكو + بترو رابغ) مع MTF aligned وحجم استثنائي.",
  "picks": [
    {
      "ticker": "2222",
      "sector": "Energy",
      "action": "شراء قوي",
      "confidence": 87,
      "reason": "🚀🚀🚀 ROCKET (87/100) - إجماع 6 إشارات: RSI + MACD + بولنجر + حجم + اختراق + power_breakout",
      "stop": 26.50,
      "target": 28.50,
      "target2": 29.80,
      "holding_days": 5,
      "risk_level": "منخفض",
      "rules_check": "EV=+3.5%✓ | RR=1:1.8✓ | weekly=صاعد✓ | score=8.5✓",
      "power_assessment": "Power=87(ROCKET)✓ - 7 فلاتر مستوفاة، أقوى إشارة كسر في universe اليوم",
      "power_score": 87,
      "power_classification": "ROCKET",
      "power_emoji": "🚀🚀🚀",
      "power_breakout_level": 27.40,
      "power_targets": {
        "T1_up": 28.50,
        "T2_up": 29.80,
        "T3_up": 31.10
      },
      "power_breakdown": {
        "volume": 25, "trend": 20, "candle": 15,
        "rsi": 15, "atr": 10, "close_pos": 10, "squeeze": 0
      }
    }
  ]
}
```

---

## ✅ Checklist النهائي

- [ ] نسخ `power_classifier.py` للمشروع
- [ ] استبدال `scanner_v9.py` بالنسخة المعدّلة
- [ ] استبدال `rules_filter.py` بالنسخة المعدّلة
- [ ] استبدال `ai_analyst_v9.py` بالنسخة المعدّلة
- [ ] `python power_classifier.py` (اختبار مستقل)
- [ ] `python scanner_v9.py` (اختبار scanner)
- [ ] فحص `tasi_candidates.json` للتأكد من حقول power_*
- [ ] `python rules_filter.py` بدون API key
- [ ] فحص reason في picks (يجب أن يبدأ بـ 🚀🚀)
- [ ] `python run_all.py` مع API key
- [ ] فحص `power_assessment` في picks
- [ ] فحص التقرير HTML بصرياً
- [ ] commit + push
- [ ] انتظار أول تقرير صباحي تلقائي
- [ ] قياس أداء ROCKETs/STRONGs لأسبوعين قبل أي معايرة

---

## 🆘 استكشاف الأخطاء

| المشكلة | السبب | الحل |
|--------|------|------|
| `ModuleNotFoundError: power_classifier` | لم تنسخ الملف | `cp power_classifier.py` للمشروع |
| كل `power_score = 0` | df فارغ أو ناقص | تأكد من `period_days=200` في scanner |
| لا تظهر ROCKETs في التقرير | حد min_score=65 صارم | طبيعي - ROCKETs نادرة (0-3 يومياً) |
| Opus لا يستخدم power_assessment | تحديث system prompt لم يُطبَّق | تأكد من نسخ ai_analyst_v9.py الجديد |
| `KeyError: 'volume'` في power | DataFrame بـ MultiIndex | حل: `df.columns = df.columns.get_level_values(0)` |

---

**تم بناء هذه الحزمة بدقة على الكود الفعلي. كل سطر مُختبر syntactically.**

عند أي مشكلة، أرسل لي:
1. آخر 20 سطر من scanner output
2. مثال candidate من tasi_candidates.json
3. الـ stack trace كاملاً
