# -*- coding: utf-8 -*-
"""
universe_weight_learning.py — V9.3
===================================
المشكلة: تعلم الأوزان كان يحدث فقط في scanner_v9.evaluate_yesterday على
~10 picks يومياً → بطيء جداً + متحيز (survivorship: نتعلم فقط مما اخترناه).
هذا سبب رئيسي لإحساس "النظام لا يتعلم": تغير الأوزان اليومي كان ~0.065
إجمالياً عبر 19 وزناً (أي ~0.003/وزن) ومعظمه decay لا تعلم.

الحل: التعلم من **كل** صفوف الكون الموسومة حديثاً (~194 صف/يوم):
    - لكل صف قُيّم اليوم (evaluated_at == اليوم): لكل إشارة نشطة فيه،
      حدّث دقة الإشارة (triggered/hit/miss) في tracker.
    - ثم اشتق الأوزان من **الدقة التراكمية الفعلية** لكل إشارة عبر الكون
      (لا نكافئ/نعاقب صفاً صفاً — بل نقيس edge كل إشارة مقابل خط الأساس):

        edge(sig)   = rate(sig) - base_rate
        target_w    = clamp(1.0 + EDGE_GAIN * edge, MIN_W, MAX_W)
        new_w       = (1-LR) * old_w + LR * target_w

    إشارة دقتها أعلى من خط أساس الكون → وزنها يرتفع نحو target؛ والعكس.
    LR = 0.15 يومياً → استجابة ملموسة خلال أسبوع، بلا تقلب عنيف.

يحفظ النتائج في:
    - tasi_weights.json (الأوزان المحدثة — يقرؤها scanner في التشغيلة نفسها)
    - tasi_tracker.json (signal_accuracy_universe — دقة كل إشارة عبر الكون)
    - weights_history/learning_log.jsonl (سجل تدقيق لكل تحديث)

يُستدعى من run_all.py في الخطوة [0] بعد evaluate_universe و rebuild_ml.
"""
import json
import glob
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
SNAPSHOTS_DIR = BASE / "universe_snapshots"
F_WEIGHTS = BASE / "tasi_weights.json"
F_TRACKER = BASE / "tasi_tracker.json"
F_LEARNING_LOG = Path("weights_history") / "learning_log.jsonl"

# معاملات التعلم
LR = 0.15            # سرعة اقتراب الوزن من الهدف يومياً
EDGE_GAIN = 8.0      # edge قدره +10pp فوق الأساس → target = 1.8
MIN_W, MAX_W = 0.2, 2.0
MIN_TRIGGERS = 30    # لا نحرك وزن إشارة قبل 30 ظهوراً (دلالة إحصائية أولية)


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    Path(tmp).replace(path)


def _rows_evaluated_on(date_str):
    """صفوف الكون التي حصلت على label بتاريخ التقييم المحدد."""
    rows = []
    for path in sorted(glob.glob(str(SNAPSHOTS_DIR / "snapshot_*.jsonl"))):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("hit") is None:
                continue
            if r.get("evaluated_at") == date_str:
                rows.append(r)
    return rows


def _all_labeled_rows():
    rows = []
    for path in sorted(glob.glob(str(SNAPSHOTS_DIR / "snapshot_*.jsonl"))):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("hit") is not None:
                rows.append(r)
    return rows


def update(today_str: str = None) -> dict:
    today_str = today_str or datetime.now().strftime("%Y-%m-%d")

    weights = _load_json(F_WEIGHTS, {})
    if not weights:
        log.warning("universe_weight_learning: لا أوزان حالية — تخطٍّ")
        return {"updated": False, "reason": "no weights file"}

    tracker = _load_json(F_TRACKER, {})
    first_run = "signal_accuracy_universe" not in tracker
    acc = tracker.setdefault("signal_accuracy_universe", {})

    # 1) حدّث عدادات الدقة بالصفوف الموسومة حديثاً اليوم فقط (بلا تكرار)
    new_rows = _rows_evaluated_on(today_str)
    for r in new_rows:
        hit = bool(r.get("hit"))
        for sig in (r.get("signals_active") or []):
            a = acc.setdefault(sig, {"triggered": 0, "hit": 0, "miss": 0, "rate": 0.0})
            a["triggered"] += 1
            if hit:
                a["hit"] += 1
            else:
                a["miss"] += 1
            a["rate"] = round(a["hit"] / max(a["triggered"], 1), 4)

    # 1ب) bootstrap في أول تشغيلة بعد الترقية: ابنِ العدادات من كل
    #     التاريخ الموسوم (آلاف الصفوف) بدل اليوم الواحد — edges أدق فوراً.
    total_triggered = sum(a["triggered"] for a in acc.values()) if acc else 0
    bootstrap = False
    if first_run or total_triggered < MIN_TRIGGERS:
        all_rows = _all_labeled_rows()
        if len(all_rows) >= 200:
            bootstrap = True
            acc.clear()
            for r in all_rows:
                hit = bool(r.get("hit"))
                for sig in (r.get("signals_active") or []):
                    a = acc.setdefault(sig, {"triggered": 0, "hit": 0, "miss": 0, "rate": 0.0})
                    a["triggered"] += 1
                    if hit:
                        a["hit"] += 1
                    else:
                        a["miss"] += 1
            for a in acc.values():
                a["rate"] = round(a["hit"] / max(a["triggered"], 1), 4)
            new_rows = all_rows  # للتقرير

    # 2) خط الأساس: hit rate للكون كله (المرجع العادل لقياس edge)
    all_rows = _all_labeled_rows()
    if not all_rows:
        _save_json(F_TRACKER, tracker)
        return {"updated": False, "reason": "no labeled rows"}
    base_rate = sum(r["hit"] for r in all_rows) / len(all_rows)

    # 3) اشتق الأوزان من edge كل إشارة
    moved = {}
    for sig, w_old in list(weights.items()):
        a = acc.get(sig)
        if not a or a["triggered"] < MIN_TRIGGERS:
            continue
        edge = a["rate"] - base_rate
        target = max(MIN_W, min(MAX_W, 1.0 + EDGE_GAIN * edge))
        w_new = round((1 - LR) * w_old + LR * target, 4)
        if abs(w_new - w_old) >= 0.0005:
            weights[sig] = w_new
            moved[sig] = {
                "old": round(w_old, 4), "new": w_new,
                "rate": a["rate"], "edge_pp": round(edge * 100, 2),
                "n": a["triggered"],
            }

    _save_json(F_WEIGHTS, weights)
    _save_json(F_TRACKER, tracker)

    # 4) سجل تدقيق
    entry = {
        "date": today_str,
        "new_labeled_rows": len(new_rows),
        "base_rate": round(base_rate, 4),
        "bootstrap": bootstrap,
        "moved": moved,
    }
    try:
        F_LEARNING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(F_LEARNING_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"learning_log: {e}")

    return {"updated": True, **entry}


def run():
    print("  🧠 تعلم الأوزان من الكون الكامل (universe-wide)...")
    s = update()
    if not s.get("updated"):
        print(f"     ⚠️ تخطٍّ: {s.get('reason')}")
        return s
    moved = s.get("moved", {})
    boot = " (bootstrap أولي)" if s.get("bootstrap") else ""
    print(f"     ✓ صفوف موسومة جديدة: {s['new_labeled_rows']}{boot} | "
          f"خط الأساس: {s['base_rate']*100:.1f}% | أوزان تحركت: {len(moved)}")
    # أبرز 5 تحركات
    top = sorted(moved.items(), key=lambda kv: -abs(kv[1]["new"] - kv[1]["old"]))[:5]
    for sig, m in top:
        arrow = "↑" if m["new"] > m["old"] else "↓"
        print(f"       {arrow} {sig}: {m['old']} → {m['new']} "
              f"(دقة {m['rate']*100:.0f}% على n={m['n']}, edge {m['edge_pp']:+.1f}pp)")
    return s


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
