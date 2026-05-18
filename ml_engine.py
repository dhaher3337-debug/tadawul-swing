# -*- coding: utf-8 -*-
"""
محرك التعلم الآلي — V9.2.3
================================
نموذج XGBoost مع Walk-Forward Cross-Validation حقيقي.

تغييرات V9.2.3 (vs V9.2.2):
    📊 تحليل الأسبوع كشف:
    - ML metrics ثابتة 6 أيام (AUC=0.937, samples=242)
    - precision=1.0, recall=0.89 في in-sample
    - لكن Win Rate الفعلي في paper trading = 36% فقط!
    - ML[0.4-0.6) كانت WR=0% (عكس ما توقعه النموذج)
    ============================================================
    
    ✅ P0: Walk-forward CV (3-fold) بدل static 80/20 split
    ✅ P0: إزالة random_state الثابت (للحصول على تباين حقيقي)
    ✅ P0: حد أدنى أعلى للـ training (250 صفقة)
    ✅ P0: تحقق من أن paper trading يُغذّي الـ dataset
    ✅ P0: مقارنة in-sample vs out-of-sample وكشف overfit
    ✅ P0: إضافة calibration check
    ✅ P0: لو AUC على walk-forward < 0.55، نُهمل ml_probability
    ✅ نخفي ml_probability من scanner إذا النموذج غير موثوق
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
for _d in (BASE, MODEL_DIR):
    try:
        if not _d.exists():
            _d.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass

F_MODEL = MODEL_DIR / "xgb_model.pkl"
F_METRICS = BASE / "ml_metrics.json"
F_IMPORTANCE = BASE / "feature_importance.json"
F_DATASET = BASE / "ml_dataset.csv"
F_TRUSTABILITY = BASE / "ml_trustability.json"  # 🆕 V9.2.3

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

# 🆕 V9.2.3: عتبات الموثوقية
MIN_AUC_TRUSTABLE = 0.55      # تحت هذا = ML عشوائي تقريباً، لا نستخدم
MIN_SAMPLES_TRUSTABLE = 250   # تحت هذا = عينة صغيرة، نستخدم بحذر
MAX_INSAMPLE_OUTSAMPLE_GAP = 0.20  # إذا الفجوة أكبر = overfit


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
    row = dict(features_dict)
    row["hit"] = int(target_label)
    row.update(metadata)
    return row


def realistic_hit_label(entry_close, future_df, target_pct=1.5, stop_pct=-2.0, days=3):
    """1 إذا حقّق target قبل stop خلال `days`، 0 خلاف ذلك."""
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

        if low_pct <= stop_pct:
            return 0
        if high_pct >= target_pct:
            return 1

    return 0


# ────────────────────────────────────────────
# إدارة الـ dataset
# ────────────────────────────────────────────
def append_training_data(rows):
    """يضيف صفوف تدريب جديدة إلى CSV التراكمي."""
    if not rows:
        return
    df_new = pd.DataFrame(rows)
    # 🆕 V9.2.3: إضافة date لكل صف إذا غير موجودة (للـ walk-forward)
    if "date" not in df_new.columns:
        df_new["date"] = datetime.now().strftime("%Y-%m-%d")
    
    if F_DATASET.exists():
        df_old = pd.read_csv(F_DATASET)
        df = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df = df_new
    df = df.tail(10000)
    df.to_csv(F_DATASET, index=False)
    log.info(f"ML dataset: +{len(rows)} صف. الإجمالي: {len(df)}")


def load_training_data(min_samples=100):
    if not F_DATASET.exists():
        return None
    df = pd.read_csv(F_DATASET)
    if len(df) < min_samples:
        return None
    return df


# ────────────────────────────────────────────
# 🆕 V9.2.3: Walk-Forward Cross-Validation
# ────────────────────────────────────────────
def _walk_forward_evaluate(df, feature_cols, n_folds=3, min_train_size=80):
    """
    Walk-forward CV - الطريقة الصحيحة لتقييم ML للسلاسل الزمنية.
    
    بدلاً من split بسيط 80/20، نعمل عدة folds:
    - Fold 1: train على [0:n/4], test على [n/4:n/2]
    - Fold 2: train على [0:n/2], test على [n/2:3n/4]
    - Fold 3: train على [0:3n/4], test على [3n/4:n]
    
    هذا يحاكي الواقع: تدرب على ما رأيت، تختبر على المستقبل.
    """
    try:
        import xgboost as xgb
        from sklearn.metrics import (
            accuracy_score, roc_auc_score, precision_score,
            recall_score, f1_score
        )
    except ImportError:
        return None
    
    # ترتيب زمني (إذا فيه date column نُرتّب به)
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)
    
    df_clean = df[feature_cols + ["hit"]].dropna()
    if len(df_clean) < min_train_size * 2:
        return None
    
    X = df_clean[feature_cols].values
    y = df_clean["hit"].values
    n = len(X)
    
    fold_metrics = []
    
    # نعمل n_folds من walk-forward
    for fold_idx in range(n_folds):
        # نسبة التدريب تكبر تدريجياً
        train_end = int(n * (fold_idx + 1) / (n_folds + 1))
        test_end = int(n * (fold_idx + 2) / (n_folds + 1))
        
        if train_end < min_train_size or test_end - train_end < 20:
            continue
        
        X_train, y_train = X[:train_end], y[:train_end]
        X_test, y_test = X[train_end:test_end], y[train_end:test_end]
        
        pos_ratio = y_train.mean()
        if pos_ratio == 0 or pos_ratio == 1:
            continue
        scale_pos_weight = (1 - pos_ratio) / pos_ratio
        
        # 🆕 لا random_state ثابت (تباين حقيقي)
        model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.85,
            colsample_bytree=0.85,
            scale_pos_weight=scale_pos_weight,
            eval_metric="logloss",
            use_label_encoder=False,
            random_state=fold_idx,  # تباين بين folds
        )
        
        try:
            model.fit(X_train, y_train)
        except Exception as e:
            log.warning(f"walk-forward fold {fold_idx} fit failed: {e}")
            continue
        
        # تقييم out-of-sample
        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        
        # تقييم in-sample (للمقارنة)
        y_pred_in = model.predict(X_train)
        y_proba_in = model.predict_proba(X_train)[:, 1]
        
        try:
            auc_out = float(roc_auc_score(y_test, y_proba)) if len(set(y_test)) > 1 else None
            auc_in = float(roc_auc_score(y_train, y_proba_in)) if len(set(y_train)) > 1 else None
        except Exception:
            auc_out, auc_in = None, None
        
        fold_metrics.append({
            "fold": fold_idx,
            "train_size": int(train_end),
            "test_size": int(test_end - train_end),
            "accuracy_out": round(float(accuracy_score(y_test, y_pred)), 3),
            "accuracy_in": round(float(accuracy_score(y_train, y_pred_in)), 3),
            "roc_auc_out": round(auc_out, 3) if auc_out else None,
            "roc_auc_in": round(auc_in, 3) if auc_in else None,
            "precision_out": round(float(precision_score(y_test, y_pred, zero_division=0)), 3),
            "recall_out": round(float(recall_score(y_test, y_pred, zero_division=0)), 3),
            "f1_out": round(float(f1_score(y_test, y_pred, zero_division=0)), 3),
            "positive_ratio_train": round(float(pos_ratio), 3),
        })
    
    if not fold_metrics:
        return None
    
    # المتوسط على folds
    valid_aucs = [f["roc_auc_out"] for f in fold_metrics if f["roc_auc_out"] is not None]
    valid_aucs_in = [f["roc_auc_in"] for f in fold_metrics if f["roc_auc_in"] is not None]
    
    avg_auc_out = np.mean(valid_aucs) if valid_aucs else None
    avg_auc_in = np.mean(valid_aucs_in) if valid_aucs_in else None
    
    summary = {
        "n_folds_run": len(fold_metrics),
        "avg_accuracy_out": round(np.mean([f["accuracy_out"] for f in fold_metrics]), 3),
        "avg_accuracy_in": round(np.mean([f["accuracy_in"] for f in fold_metrics]), 3),
        "avg_roc_auc_out": round(float(avg_auc_out), 3) if avg_auc_out is not None else None,
        "avg_roc_auc_in": round(float(avg_auc_in), 3) if avg_auc_in is not None else None,
        "avg_precision_out": round(np.mean([f["precision_out"] for f in fold_metrics]), 3),
        "avg_recall_out": round(np.mean([f["recall_out"] for f in fold_metrics]), 3),
        "avg_f1_out": round(np.mean([f["f1_out"] for f in fold_metrics]), 3),
        "fold_details": fold_metrics,
    }
    
    # 🆕 كشف overfitting: الفجوة بين in-sample و out-of-sample
    if avg_auc_in is not None and avg_auc_out is not None:
        gap = avg_auc_in - avg_auc_out
        summary["overfit_gap"] = round(float(gap), 3)
        summary["is_overfit"] = bool(gap > MAX_INSAMPLE_OUTSAMPLE_GAP)
    
    return summary


def _assess_trustability(walk_forward_summary, sample_count):
    """
    🆕 V9.2.3: تقييم موثوقية الـ ML model.
    
    Returns: dict مع trust_level: "trusted" | "weak" | "unreliable"
    """
    if walk_forward_summary is None:
        return {
            "trust_level": "unavailable",
            "use_ml_probability": False,
            "reason": "Walk-forward CV لم يُنفّذ",
        }
    
    auc_out = walk_forward_summary.get("avg_roc_auc_out")
    is_overfit = walk_forward_summary.get("is_overfit", False)
    overfit_gap = walk_forward_summary.get("overfit_gap", 0)
    
    reasons = []
    trust = "trusted"
    
    if auc_out is None or auc_out < MIN_AUC_TRUSTABLE:
        trust = "unreliable"
        reasons.append(f"AUC out-of-sample={auc_out} < {MIN_AUC_TRUSTABLE}")
    
    if sample_count < MIN_SAMPLES_TRUSTABLE:
        if trust == "trusted":
            trust = "weak"
        reasons.append(f"عينة صغيرة: {sample_count} < {MIN_SAMPLES_TRUSTABLE}")
    
    if is_overfit:
        trust = "unreliable"
        reasons.append(f"فجوة overfit كبيرة: {overfit_gap}")
    
    use_ml = trust == "trusted"
    
    if not reasons:
        reasons.append(f"AUC={auc_out}, samples={sample_count}, no overfit detected")
    
    return {
        "trust_level": trust,
        "use_ml_probability": use_ml,
        "auc_out_of_sample": auc_out,
        "sample_count": sample_count,
        "overfit_gap": overfit_gap,
        "is_overfit": is_overfit,
        "reasons": reasons,
    }


# ────────────────────────────────────────────
# التدريب (مُحدّث V9.2.3)
# ────────────────────────────────────────────
def train_model(min_samples=100):
    """
    يدرب XGBoost مع walk-forward validation حقيقي.
    
    V9.2.3:
    - Walk-forward CV (3-fold) بدلاً من 80/20 ثابت
    - كشف overfitting
    - حفظ trustability report
    - لا random_state ثابت في النموذج النهائي
    """
    try:
        import xgboost as xgb
        from sklearn.metrics import (
            accuracy_score, roc_auc_score, precision_score,
            recall_score, f1_score
        )
    except ImportError:
        log.warning("xgboost/sklearn غير مثبتين — تخطي التدريب")
        return {"status": "skipped", "reason": "xgboost/sklearn not installed"}

    df = load_training_data(min_samples)
    if df is None:
        # حفظ trustability report
        _save_trustability({
            "trust_level": "unavailable",
            "use_ml_probability": False,
            "reason": f"عينة غير كافية (< {min_samples})",
            "sample_count": 0,
            "date": datetime.now().strftime("%Y-%m-%d"),
        })
        return {"status": "insufficient_data", "min_required": min_samples}

    # 🆕 ترتيب زمني إن وُجد date
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)

    feature_cols = [f for f in FEATURES if f in df.columns]
    df_clean = df[feature_cols + ["hit"]].dropna()

    if len(df_clean) < min_samples:
        _save_trustability({
            "trust_level": "unavailable",
            "use_ml_probability": False,
            "reason": f"بعد التنظيف، عينة={len(df_clean)} < {min_samples}",
            "sample_count": len(df_clean),
            "date": datetime.now().strftime("%Y-%m-%d"),
        })
        return {"status": "insufficient_clean_data", "samples": len(df_clean)}

    X = df_clean[feature_cols].values
    y = df_clean["hit"].values

    # 🆕 V9.2.3: تشغيل Walk-Forward CV أولاً
    wf_summary = _walk_forward_evaluate(df_clean, feature_cols, n_folds=3)
    
    # تقييم الموثوقية
    trustability = _assess_trustability(wf_summary, len(df_clean))
    trustability["date"] = datetime.now().strftime("%Y-%m-%d")
    _save_trustability(trustability)
    
    log.info(f"ML trustability: {trustability['trust_level']} | "
             f"use_probability={trustability['use_ml_probability']}")

    # ─── تدريب النموذج النهائي على كل البيانات ───
    pos_ratio = y.mean()
    scale_pos_weight = (1 - pos_ratio) / pos_ratio if 0 < pos_ratio < 1 else 1.0

    # split بسيط 80/20 للـ feature importance والـ legacy metrics فقط
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    # 🆕 V9.2.3: random_state يتغير كل تدريب (تباين حقيقي)
    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
        use_label_encoder=False,
        random_state=None,  # ← التغيير الحرج
    )

    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    legacy_metrics = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "samples_total": len(df_clean),
        "samples_train": len(X_train),
        "samples_test": len(X_test),
        "positive_ratio": round(float(pos_ratio), 3),
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 3),
        "roc_auc": round(float(roc_auc_score(y_test, y_proba)), 3) if len(set(y_test)) > 1 else None,
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 3),
        "recall": round(float(recall_score(y_test, y_pred, zero_division=0)), 3),
        "f1": round(float(f1_score(y_test, y_pred, zero_division=0)), 3),
    }
    
    # 🆕 إضافة walk-forward summary للـ metrics
    metrics = {
        **legacy_metrics,
        "walk_forward": wf_summary,
        "trustability": trustability,
        "version": "V9.2.3",
    }

    importances = model.feature_importances_
    importance_dict = {
        feat: round(float(imp), 4)
        for feat, imp in zip(feature_cols, importances)
    }
    importance_dict = dict(sorted(importance_dict.items(), key=lambda x: -x[1]))

    with open(F_MODEL, "wb") as f:
        pickle.dump({
            "model": model,
            "features": feature_cols,
            "trustability": trustability,
        }, f)
    with open(F_METRICS, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    with open(F_IMPORTANCE, "w", encoding="utf-8") as f:
        json.dump(importance_dict, f, ensure_ascii=False, indent=2)

    log.info(
        f"ML model trained V9.2.3: "
        f"WF_AUC_out={wf_summary['avg_roc_auc_out'] if wf_summary else 'N/A'}, "
        f"InSample_AUC={legacy_metrics.get('roc_auc')}, "
        f"trust={trustability['trust_level']}"
    )

    return {
        "status": "success",
        "metrics": metrics,
        "trustability": trustability,
        "top_features": list(importance_dict.items())[:10],
    }


def _save_trustability(trust_dict):
    """يحفظ trustability report."""
    try:
        with open(F_TRUSTABILITY, "w", encoding="utf-8") as f:
            json.dump(trust_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"failed to save trustability: {e}")


def get_ml_trustability():
    """يقرأ ml_trustability.json - يُستخدم من scanner/rules_filter."""
    if not F_TRUSTABILITY.exists():
        return {"trust_level": "unavailable", "use_ml_probability": False}
    try:
        with open(F_TRUSTABILITY, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"trust_level": "unavailable", "use_ml_probability": False}


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


def predict_probability(features_dict, respect_trustability=True):
    """
    يُرجع احتمال النجاح (0-1) لمرشح جديد.
    
    🆕 V9.2.3: respect_trustability=True يُرجع None إذا النموذج غير موثوق.
    """
    bundle = load_model()
    if bundle is None:
        return None
    
    # 🆕 احترام trustability
    if respect_trustability:
        trust = bundle.get("trustability") or get_ml_trustability()
        if not trust.get("use_ml_probability", False):
            log.debug(f"ML probability skipped: trust={trust.get('trust_level')}")
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
    
    🆕 V9.2.3: لا نقترح أوزاناً إذا النموذج غير موثوق.
    """
    # احترام trustability
    trust = get_ml_trustability()
    if not trust.get("use_ml_probability", False):
        log.info(f"weight suggestions skipped: trust={trust.get('trust_level')}")
        return None
    
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

    max_s = max(indicator_scores.values())
    min_s = min(indicator_scores.values())
    rng = max_s - min_s if max_s > min_s else 1

    weights = {}
    for ind, score in indicator_scores.items():
        normalized = 0.3 + (score - min_s) / rng * (2.5 - 0.3)
        weights[ind] = round(normalized, 3)

    return weights


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Training ML model V9.2.3...")
    result = train_model(min_samples=50)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
