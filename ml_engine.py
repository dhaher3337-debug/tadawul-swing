# -*- coding: utf-8 -*-
"""
محرك التعلم الآلي — V9
================================
نموذج XGBoost حقيقي بدلاً من مضاعفات *1.02/*0.97 الوهمية في V8.

كيف يعمل:
  1. يجمع dataset من آخر 90 يوم: (indicator_values → hit/miss)
  2. يدرب XGBoost Classifier يومياً
  3. يستخدم النموذج لتوقع احتمال النجاح لكل مرشح جديد
  4. feature_importance من النموذج يُستخدم لتحديث الأوزان

التعريف الواقعي لـ "hit":
  - الارتفاع في 3 أيام >= 1.5% مع عدم اختراق وقف 2% قبل ذلك
  - هذا يطابق أسلوب العميل في swing trading

المخرجات:
  - tadawul_data/ml_model.pkl
  - tadawul_data/feature_importance.json
  - tadawul_data/ml_metrics.json (accuracy, ROC-AUC, precision, recall)
"""
import json
import pickle
import logging
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
MODEL_DIR = Path("ml_models")
# إنشاء آمن بدون mkdir مباشر (يتجنب FileExistsError race condition)
for _d in (BASE, MODEL_DIR):
    try:
        if not _d.exists():
            _d.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass  # موجود مسبقاً، آمن تجاهله
F_MODEL = MODEL_DIR / "xgb_model.pkl"
F_METRICS = BASE / "ml_metrics.json"
F_IMPORTANCE = BASE / "feature_importance.json"
F_DATASET = BASE / "ml_dataset.csv"

# المميزات التي يتعلم عليها النموذج
FEATURES = [
    "rsi", "stoch_rsi_k", "macd_hist", "bb_pct", "bb_width",
    "adx", "di_plus", "di_minus",
    "mfi", "cmf", "fib_pos",
    "supertrend_dir", "weekly_trend", "rs_vs_tasi",
    "vol_ratio", "vwap_diff_pct",
    "dist_from_sma20_pct", "dist_from_sma50_pct",
    "obv_vs_ma_pct",
]


def _safe_get(row, key, default=0.0):
    v = row.get(key, default)
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def extract_features_from_snapshot(row, close):
    """يستخرج vector المميزات من صف مؤشرات سهم واحد."""
    vwap = _safe_get(row, "vwap", close)
    sma20 = _safe_get(row, "sma20", close)
    sma50 = _safe_get(row, "sma50", close)
    obv_val = _safe_get(row, "obv", 0)
    obv_ma = _safe_get(row, "obv_ma", 1)

    return {
        "rsi": _safe_get(row, "rsi", 50),
        "stoch_rsi_k": _safe_get(row, "stoch_rsi_k", 50),
        "macd_hist": _safe_get(row, "macd_hist", 0),
        "bb_pct": _safe_get(row, "bb_pct", 0.5),
        "bb_width": _safe_get(row, "bb_width", 0.05),
        "adx": _safe_get(row, "adx", 20),
        "di_plus": _safe_get(row, "di_plus", 20),
        "di_minus": _safe_get(row, "di_minus", 20),
        "mfi": _safe_get(row, "mfi", 50),
        "cmf": _safe_get(row, "cmf", 0),
        "fib_pos": _safe_get(row, "fib_pos", 0.5),
        "supertrend_dir": _safe_get(row, "supertrend_dir", 0),
        "weekly_trend": _safe_get(row, "weekly_trend", 0),
        "rs_vs_tasi": _safe_get(row, "rs_vs_tasi", 1.0),
        "vol_ratio": _safe_get(row, "vol_ratio", 1.0),
        "vwap_diff_pct": (close - vwap) / vwap * 100 if vwap else 0,
        "dist_from_sma20_pct": (close - sma20) / sma20 * 100 if sma20 else 0,
        "dist_from_sma50_pct": (close - sma50) / sma50 * 100 if sma50 else 0,
        "obv_vs_ma_pct": (obv_val - obv_ma) / abs(obv_ma) * 100 if obv_ma else 0,
    }


def build_training_row(features_dict, target_label, metadata):
    """يبني صف تدريب: مميزات + هدف + meta."""
    row = dict(features_dict)
    row["hit"] = int(target_label)
    row.update(metadata)
    return row


def realistic_hit_label(entry_close, future_df, target_pct=1.5, stop_pct=-2.0, days=3):
    """
    يُرجع 1 إذا السهم حقق target_pct خلال `days` أيام دون اختراق stop_pct أولاً.
    يُرجع 0 إذا اخترق الوقف أو لم يحقق الهدف.
    يُرجع None إذا البيانات غير كافية.

    هذا تعريف "hit" الواقعي — ليس "ارتفع ولو 0.01%" كما في V8.
    """
    if future_df is None or future_df.empty or entry_close <= 0:
        return None

    window = future_df.head(days)
    if len(window) < 1:
        return None

    for _, bar in window.iterrows():
        low = float(bar.get("Low", bar.get("Close", 0)))
        high = float(bar.get("High", bar.get("Close", 0)))
        low_pct = (low - entry_close) / entry_close * 100
        high_pct = (high - entry_close) / entry_close * 100

        # الوقف ضُرب أولاً
        if low_pct <= stop_pct:
            return 0
        # الهدف ضُرب
        if high_pct >= target_pct:
            return 1

    return 0  # انتهت المدة دون تحقق الهدف


# ────────────────────────────────────────────
# إدارة الـ dataset التراكمي
# ────────────────────────────────────────────
def append_training_data(rows):
    """يضيف صفوف تدريب جديدة إلى CSV التراكمي."""
    if not rows:
        return
    df_new = pd.DataFrame(rows)
    if F_DATASET.exists():
        df_old = pd.read_csv(F_DATASET)
        df = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df = df_new
    # الاحتفاظ بآخر 10,000 صف كحد أقصى
    df = df.tail(10000)
    df.to_csv(F_DATASET, index=False)
    log.info(f"ML dataset: +{len(rows)} صف. الإجمالي: {len(df)}")


def load_training_data(min_samples=100):
    """يُحمّل dataset ويتحقق من كفايته."""
    if not F_DATASET.exists():
        return None
    df = pd.read_csv(F_DATASET)
    if len(df) < min_samples:
        return None
    return df


# ────────────────────────────────────────────
# التدريب
# ────────────────────────────────────────────
def train_model(min_samples=100):
    """
    يدرب XGBoost Classifier ويحفظ النموذج + المقاييس + feature importance.
    يُرجع dict بالنتائج.
    """
    try:
        import xgboost as xgb
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import accuracy_score, roc_auc_score, precision_score, recall_score, f1_score
    except ImportError:
        log.warning("xgboost/sklearn غير مثبتين — تخطي التدريب")
        return {"status": "skipped", "reason": "xgboost/sklearn not installed"}

    df = load_training_data(min_samples)
    if df is None:
        return {"status": "insufficient_data", "min_required": min_samples}

    # تنظيف
    feature_cols = [f for f in FEATURES if f in df.columns]
    df = df[feature_cols + ["hit"]].dropna()

    if len(df) < min_samples:
        return {"status": "insufficient_clean_data", "samples": len(df)}

    X = df[feature_cols].values
    y = df["hit"].values

    # توازن الفئات
    pos_ratio = y.mean()
    scale_pos_weight = (1 - pos_ratio) / pos_ratio if 0 < pos_ratio < 1 else 1.0

    # تقسيم زمني (آخر 20% للاختبار)
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
        random_state=42,
        use_label_encoder=False,
    )

    model.fit(X_train, y_train)

    # التقييم
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    metrics = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "samples_total": len(df),
        "samples_train": len(X_train),
        "samples_test": len(X_test),
        "positive_ratio": round(float(pos_ratio), 3),
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 3),
        "roc_auc": round(float(roc_auc_score(y_test, y_proba)), 3) if len(set(y_test)) > 1 else None,
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 3),
        "recall": round(float(recall_score(y_test, y_pred, zero_division=0)), 3),
        "f1": round(float(f1_score(y_test, y_pred, zero_division=0)), 3),
    }

    # feature importance
    importances = model.feature_importances_
    importance_dict = {
        feat: round(float(imp), 4)
        for feat, imp in zip(feature_cols, importances)
    }
    importance_dict = dict(sorted(importance_dict.items(), key=lambda x: -x[1]))

    # حفظ
    with open(F_MODEL, "wb") as f:
        pickle.dump({"model": model, "features": feature_cols}, f)
    with open(F_METRICS, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    with open(F_IMPORTANCE, "w", encoding="utf-8") as f:
        json.dump(importance_dict, f, ensure_ascii=False, indent=2)

    log.info(f"ML model trained: AUC={metrics.get('roc_auc')}, "
             f"Acc={metrics['accuracy']}, F1={metrics['f1']}")

    return {
        "status": "success",
        "metrics": metrics,
        "top_features": list(importance_dict.items())[:10],
    }


# ────────────────────────────────────────────
# التنبؤ
# ────────────────────────────────────────────
def load_model():
    if not F_MODEL.exists():
        return None
    try:
        with open(F_MODEL, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        log.warning(f"Load model failed: {e}")
        return None


def predict_probability(features_dict):
    """
    يُرجع احتمال النجاح (0-1) لمرشح جديد.
    إذا النموذج غير موجود يُرجع None.
    """
    bundle = load_model()
    if bundle is None:
        return None
    model = bundle["model"]
    feat_cols = bundle["features"]
    X = np.array([[features_dict.get(f, 0.0) for f in feat_cols]])
    try:
        prob = model.predict_proba(X)[0][1]
        return float(prob)
    except Exception as e:
        log.warning(f"Predict failed: {e}")
        return None


# ────────────────────────────────────────────
# تحويل feature importance إلى أوزان indicator
# ────────────────────────────────────────────
FEATURE_TO_INDICATOR = {
    "rsi": "rsi",
    "stoch_rsi_k": "stoch_rsi",
    "macd_hist": "macd",
    "bb_pct": "bollinger",
    "bb_width": "bollinger",
    "adx": "adx",
    "di_plus": "adx",
    "di_minus": "adx",
    "mfi": "mfi",
    "cmf": "cmf",
    "fib_pos": "fibonacci",
    "supertrend_dir": "supertrend",
    "weekly_trend": "weekly_trend",
    "rs_vs_tasi": "relative_strength",
    "vol_ratio": "volume_surge",
    "vwap_diff_pct": "vwap",
    "dist_from_sma20_pct": "sma_cross",
    "dist_from_sma50_pct": "sma_cross",
    "obv_vs_ma_pct": "obv",
}


def suggest_weights_from_importance():
    """
    يحول feature_importance إلى أوزان للـ indicators.
    نجمع أهمية كل indicator من مميزاته ثم نُطبع لـ 0.3-2.5.
    """
    if not F_IMPORTANCE.exists():
        return None

    with open(F_IMPORTANCE, encoding="utf-8") as f:
        importances = json.load(f)

    indicator_scores = {}
    for feat, imp in importances.items():
        ind = FEATURE_TO_INDICATOR.get(feat)
        if ind:
            indicator_scores[ind] = indicator_scores.get(ind, 0) + imp

    if not indicator_scores:
        return None

    # تطبيع: أعلى أهمية → 2.5، أدنى → 0.3
    max_s = max(indicator_scores.values())
    min_s = min(indicator_scores.values())
    rng = max_s - min_s if max_s > min_s else 1

    weights = {}
    for ind, score in indicator_scores.items():
        normalized = 0.3 + (score - min_s) / rng * (2.5 - 0.3)
        weights[ind] = round(normalized, 3)

    return weights
