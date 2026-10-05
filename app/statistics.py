# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Statistik aus dem Druckverlauf: Drucke, Filament und Kosten der letzten zwölf Monate.

Filament und Kosten zählen nur fertige Drucke. Grundlage sind die Angaben aus der
gedruckten Datei (Gewicht und Filamente der geslicten Platten); der Strom ergibt
sich aus der tatsächlichen Druckdauer, wo der Drucker sie meldet, sonst aus der
geplanten Druckzeit.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

from . import spoolman, store

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
    """Je Datei: Gewicht, Filamente, Materialkosten und geplante Zeit der geslicten Platten."""
    facts: dict[str, dict] = {}
    rules = store.price_rules(settings)
    spool_price = spoolman.price_resolver(settings)
    for r in con.execute(
            """SELECT f.path, f.printer, p.weight_g, p.cost, p.prediction_s, p.filaments_json
               FROM plates p JOIN files f ON f.id = p.file_id WHERE p.sliced = 1"""):
        fact = facts.setdefault(r["path"], {"printer": r["printer"], "weight_g": 0.0, "cost": None,
                                            "prediction_s": 0, "filaments": []})
        fact["weight_g"] += r["weight_g"] or 0
        fact["prediction_s"] += r["prediction_s"] or 0
        cost = store.material_cost(settings, json.loads(r["filaments_json"] or "[]"), rules, spool_price)
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
    by_spool: dict[str, dict] = {}   # nur mit Spoolman: Verbrauch je Spule bzw. Spoolman-Filament
    printers: dict[str, dict] = {}
    totals = {"completed": 0, "failed": 0, "hours": 0.0, "filament_g": 0.0, "cost": 0.0, "has_cost": False}

    with store.connect() as con:
        settings = store.get_settings(con)
        facts = _file_facts(con, settings)
        jobs = con.execute(
            """SELECT id, printer_name, local_path, status, started, ended, duration_s FROM print_jobs
               WHERE hidden = 0 AND status IN ('completed', 'cancelled', 'failed') AND started >= ?""",
            (t0,)).fetchall()
    spool_on = bool(spoolman.url(settings))
    all_spools = spoolman.spools(settings) if spool_on else []
    booked = spoolman.bookings_for([j["id"] for j in jobs]) if spool_on else {}

    def add_spool(key, label, spool_id, grams):
        entry = by_spool.setdefault(key, {"label": label, "spool_id": spool_id, "grams": 0.0})
        entry["grams"] += grams

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
        if not spool_on:
            continue
        rows = [b for b in booked.get(j["id"], []) if b["spool_id"]]
        if rows:  # gebucht: genau diese Spule (auch von Hand gewählte)
            for b in rows:
                add_spool(f"spool:{b['spool_id']}", b["spool_label"], b["spool_id"], b["grams"])
            continue
        for f in fact["filaments"]:
            grams = f.get("used_g") or 0
            found = spoolman.match(f, all_spools) if grams else None
            if not grams:
                continue
            if found and len(found) == 1:
                add_spool(f"spool:{found[0].get('id')}", spoolman.label(found[0]), found[0].get("id"), grams)
            elif found:  # mehrere Spulen desselben Filaments: dem Filament zuordnen
                ids = ", ".join(f"#{s.get('id')}" for s in found)
                name = spoolman.label(found[0]).split(" ", 1)[-1]
                add_spool(f"fil:{(found[0].get('filament') or {}).get('id')}", f"{ids} {name}", None, grams)
            else:
                add_spool("none", "", None, grams)

    month_list = [months[k] for k in keys]
    for m in month_list:
        year, mon = m["key"].split("-")
        m["year"], m["month"] = int(year), int(mon)
    printer_list = sorted(printers.values(), key=lambda p: -(p["completed"] + p["failed"]))
    for p in printer_list:
        p["total"] = p["completed"] + p["failed"]
        p["rate"] = p["completed"] / p["total"] * 100 if p["total"] else 0
    material_list = sorted(({"name": k, "grams": v} for k, v in materials.items() if v > 0), key=lambda m: -m["grams"])
    spool_list = sorted((s for s in by_spool.values() if s["grams"] > 0), key=lambda s: (s["label"] == "", -s["grams"]))
    return {
        "months": month_list,
        "max_prints": max((m["completed"] + m["failed"] for m in month_list), default=0),
        "max_cost": max((m["cost"] for m in month_list), default=0),
        "materials": material_list,
        "max_material": material_list[0]["grams"] if material_list else 0,
        "spoolman": spool_on,
        "spools": spool_list,
        "max_spool": max((s["grams"] for s in spool_list), default=0),
        "printers": printer_list,
        "totals": totals,
    }
