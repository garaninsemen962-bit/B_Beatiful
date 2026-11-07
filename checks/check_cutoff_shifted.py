# -*- coding: utf-8 -*-
"""Порог отсечения (cutOff) сдвинулся по продукту.

Сдвиг порога меняет решение у всех клиентов, чей балл попал между старым
и новым значением. Строка показывается на каждую пару «продукт, старый -> новый
порог», отдельно считается, у скольких из этих заявок сменилось итоговое решение.
"""

CUTOFF_PATH = "decisionResult.product_trials.cutOff.value"
DEFAULTS = {"low_confidence_cnt": 5, "app_ids_limit": 20}


def _product_names(segments):
    names = {}
    for s in segments:
        details = s.get("PRODUCT_DETAILS") or {}
        for side in ("BEFORE", "AFTER"):
            for pid, d in (details.get(side) or {}).items():
                if isinstance(d, dict) and d.get("product_name"):
                    names[str(pid)] = d["product_name"]
    return names


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()
    segments = list(mart.segments())
    names = _product_names(segments)
    decision_changed = {s["APP_ID"] for s in segments if s.get("DECISION_CHANGED")}

    groups = {}
    for r in mart.rows(path=CUTOFF_PATH, change_type="VALUE_CHANGED"):
        pid = (r.get("ARRAY_KEY") or "").split("|")[0]
        key = (names.get(pid, pid), r.get("VALUE_BEFORE"), r.get("VALUE_AFTER"))
        groups.setdefault(key, set()).add(r["APP_ID"])

    rows = []
    for (product, before, after), apps in sorted(groups.items(), key=lambda x: -len(x[1])):
        flipped = len(apps & decision_changed)
        rows.append({
            "LABEL": CHECK["label"],
            "SOURCE": "AFTER",
            "ARGUMENT": f"{product}: {before} → {after}; решение сменилось у {flipped}",
            "CNT": len(apps),
            "TOTAL": total,
            "METRIC": round(len(apps) / total, 4) if total else 0.0,
            "APP_IDS": sorted(apps)[:p["app_ids_limit"]],
            "RESULT": "FAIL",
            "CONFIDENCE": "LOW" if len(apps) < p["low_confidence_cnt"] else "HIGH",
        })
    if not rows:
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": "",
                     "CNT": 0, "TOTAL": total, "METRIC": 0.0, "APP_IDS": [],
                     "RESULT": "PASS", "CONFIDENCE": "HIGH"})
    return rows


CHECK = {"id": "cutoff_shifted",
         "label": "Порог сдвинулся",
         "params": DEFAULTS,
         "entrypoint": run}
