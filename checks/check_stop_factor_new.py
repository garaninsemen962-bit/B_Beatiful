# -*- coding: utf-8 -*-
"""Новый стоп-фактор: правило появилось в стратегии и сработало, либо hit false -> true.

Парная проверка к «Стоп-фактор перестал работать». Новое правило-отсекатель
переводит часть одобренных заявок в отказ, поэтому его нужно видеть отдельно.
"""

RULE_HIT_PATH = "decisionResult.product_trials.rules.hit"
DEFAULTS = {"low_confidence_cnt": 5, "app_ids_limit": 20}


def _rule_name(array_key):
    # ARRAY_KEY здесь имеет вид "<product_id>|<rule_name>"
    parts = (array_key or "").split("|")
    return parts[-1] if parts else ""


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    added, turned_on = {}, {}

    for r in mart.rows(path=RULE_HIT_PATH, change_type="ATTRIBUTE_NEW"):
        if r.get("VALUE_AFTER") == "true":
            added.setdefault(_rule_name(r["ARRAY_KEY"]), set()).add(r["APP_ID"])

    for r in mart.rows(path=RULE_HIT_PATH, change_type="VALUE_CHANGED"):
        if r.get("VALUE_BEFORE") in ("false", "NULL", "") and r.get("VALUE_AFTER") == "true":
            turned_on.setdefault(_rule_name(r["ARRAY_KEY"]), set()).add(r["APP_ID"])

    total = mart.total_apps()
    rows = []
    for title, bucket in (("правило добавлено в стратегию", added),
                          ("правило начало срабатывать", turned_on)):
        for rule, apps in sorted(bucket.items()):
            rows.append({
                "LABEL": CHECK["label"],
                "SOURCE": "AFTER",
                "ARGUMENT": f"{rule} ({title})",
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


CHECK = {"id": "stop_factor_new",
         "label": "Стоп-фактор новый",
         "params": DEFAULTS,
         "entrypoint": run}
