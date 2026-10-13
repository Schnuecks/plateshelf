# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Statistics from the print history: prints, filament and costs of the last twelve months.

Filament and costs only count finished prints. They are based on the data from the
printed file (weight and filaments of the sliced plates); electricity is calculated
from the actual print duration where the printer reports it, otherwise from the
planned print time.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

from . import store

MONTHS = 12


def _month_keys(now: float) -> list[str]:
    t = time.localtime(now)
    year, month = t.tm_year, t.tm_mon
    keys = []
    for _ in range(MONTHS):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return keys[::-1]


def _file_facts(con, settings: dict) -> dict[str, dict]:
    """Per file: weight, filaments, material costs and planned time of the sliced plates."""
    facts: dict[str, dict] = {}
    rules = store.price_rules(settings)
    for r in con.execute(
            """SELECT f.path, f.printer, p.weight_g, p.cost, p.prediction_s, p.filaments_json
               FROM plates p JOIN files f ON f.id = p.file_id WHERE p.sliced = 1"""):
        fact = facts.setdefault(r["path"], {"printer": r["printer"], "weight_g": 0.0, "cost": None,
                                            "prediction_s": 0, "filaments": []})
        fact["weight_g"] += r["weight_g"] or 0
        fact["prediction_s"] += r["prediction_s"] or 0
        cost = store.material_cost(settings, json.loads(r["filaments_json"] or "[]"), rules)
        if cost is not None:
            fact["cost"] = (fact["cost"] or 0) + cost
        fact["filaments"] += json.loads(r["filaments_json"] or "[]")
    return facts


def compute(now: float | None = None) -> dict:
    now = now or time.time()
    keys = _month_keys(now)
    t0 = time.mktime(time.strptime(keys[0] + "-01", "%Y-%m-%d"))
    months = {k: {"key": k, "completed": 0, "failed": 0, "cost": 0.0, "filament_g": 0.0} for k in keys}
    materials: dict[str, float] = defaultdict(float)
    printers: dict[str, dict] = {}
    totals = {"completed": 0, "failed": 0, "hours": 0.0, "filament_g": 0.0, "cost": 0.0, "has_cost": False}

    with store.connect() as con:
        settings = store.get_settings(con)
        facts = _file_facts(con, settings)
        jobs = con.execute(
            """SELECT printer_name, local_path, status, started, ended, duration_s FROM print_jobs
               WHERE hidden = 0 AND status IN ('completed', 'cancelled', 'failed') AND started >= ?""",
            (t0,)).fetchall()

    for j in jobs:
        month = months.get(time.strftime("%Y-%m", time.localtime(j["started"])))
        if month is None:
            continue
        per_printer = printers.setdefault(j["printer_name"] or "?", {"name": j["printer_name"] or "?",
                                                                    "completed": 0, "failed": 0})
        if j["status"] != "completed":
            month["failed"] += 1
            per_printer["failed"] += 1
            totals["failed"] += 1
            continue
        month["completed"] += 1
        per_printer["completed"] += 1
        totals["completed"] += 1
        duration = j["duration_s"] or ((j["ended"] - j["started"]) if j["ended"] and j["started"] else 0)
        fact = facts.get(j["local_path"] or "")
        if not duration and fact:
            duration = fact["prediction_s"]
        totals["hours"] += duration / 3600
        if not fact:
            continue
        watts = store.printer_watts(settings, fact["printer"])
        cost = (fact["cost"] or 0) + duration / 3600 * watts / 1000 * settings["strompreis"]
        month["cost"] += cost
        month["filament_g"] += fact["weight_g"]
        totals["cost"] += cost
        totals["filament_g"] += fact["weight_g"]
        totals["has_cost"] = True
        for f in fact["filaments"]:
            materials[f.get("type") or "?"] += f.get("used_g") or 0

    month_list = [months[k] for k in keys]
    for m in month_list:
        year, mon = m["key"].split("-")
        m["year"], m["month"] = int(year), int(mon)
    printer_list = sorted(printers.values(), key=lambda p: -(p["completed"] + p["failed"]))
    for p in printer_list:
        p["total"] = p["completed"] + p["failed"]
        p["rate"] = p["completed"] / p["total"] * 100 if p["total"] else 0
    material_list = sorted(({"name": k, "grams": v} for k, v in materials.items() if v > 0), key=lambda m: -m["grams"])
    return {
        "months": month_list,
        "max_prints": max((m["completed"] + m["failed"] for m in month_list), default=0),
        "max_cost": max((m["cost"] for m in month_list), default=0),
        "materials": material_list,
        "max_material": material_list[0]["grams"] if material_list else 0,
        "printers": printer_list,
        "totals": totals,
    }
