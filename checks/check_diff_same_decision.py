# -*- coding: utf-8 -*-
"""Различия есть, решение то же.

Изменение стратегии затронуло ответ, но итоговое решение не поменялось:
правка «прошла мимо» либо готовит эффект, который проявится позже.
Информационная проверка (PASS): показывает охват и самые частые изменённые блоки.
"""

DEFAULTS = {"app_ids_limit": 20, "top_blocks": 5}


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()
    changed_decision = {s["APP_ID"] for s in mart.segments() if s.get("DECISION_CHANGED")}

    blocks, apps = {}, set()
    for r in mart.rows(diff_flag=1):
        if r["APP_ID"] in changed_decision:
            continue
        apps.add(r["APP_ID"])
        block = ".".join(x for x in (r.get("PREFIX"), r.get("CLASS"), r.get("SUFFIX")) if x and x != "NULL")
        blocks.setdefault(block or r["PATH"], set()).add(r["APP_ID"])

    rows = [{
        "LABEL": CHECK["label"],
        "SOURCE": "AFTER",
        "ARGUMENT": "все изменённые атрибуты",
        "CNT": len(apps),
        "TOTAL": total,
        "METRIC": round(len(apps) / total, 4) if total else 0.0,
        "APP_IDS": sorted(apps)[:p["app_ids_limit"]],
        "RESULT": "PASS",
        "CONFIDENCE": "HIGH",
    }]
    for block, ids in sorted(blocks.items(), key=lambda x: -len(x[1]))[:p["top_blocks"]]:
        rows.append({
            "LABEL": CHECK["label"],
            "SOURCE": "AFTER",
            "ARGUMENT": block,
            "CNT": len(ids),
            "TOTAL": total,
            "METRIC": round(len(ids) / total, 4) if total else 0.0,
            "APP_IDS": sorted(ids)[:p["app_ids_limit"]],
            "RESULT": "PASS",
            "CONFIDENCE": "HIGH",
        })
    return rows


CHECK = {"id": "diff_same_decision",
         "label": "Различия есть, решение то же",
         "params": DEFAULTS,
         "entrypoint": run}
