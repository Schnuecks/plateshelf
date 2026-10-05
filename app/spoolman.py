# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Schnuecks
"""Optionale Anbindung an Spoolman (Spulenverwaltung, https://github.com/Donkie/Spoolman).

Stufe 1, nur lesen: Spulen werden über Farbe und Material den Filamenten der Dateien
zugeordnet. Daraus kommen Preise (zwischen eigenen Preisregeln und dem Preis aus der Datei)
und die Restmenge („reicht die Rolle?“) auf der Projektseite.

Stufe 2, nur wenn eingeschaltet: Der Verbrauch fertiger Drucke wird an Spoolman gemeldet.
Gebucht wird nur, was eindeutig einer Spule zuzuordnen ist und nach dem Einschalten fertig
wurde, jeder Druck höchstens einmal (Tabelle spoolman_bookings). Passen mehrere Spulen, wird
nicht geraten: Im Druckverlauf wählt man die Spule selbst. Wer den Verbrauch schon anders
bucht (Moonraker, AMS-Tools), lässt Stufe 2 aus.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from . import store

log = logging.getLogger("plateshelf")

URL_KEY = "spoolman_url"
BOOK_KEY = "spoolman_buchen_seit"   # Zeitpunkt, ab dem fertige Drucke gebucht werden
CACHE_SECONDS = 60
TIMEOUT = 8

state: dict = {"ok": None, "error": "", "at": 0.0, "spools": []}
_lock = threading.Lock()
_book_lock = threading.Lock()


def url(settings: dict | None = None) -> str:
    """Adresse aus SPOOLMAN_URL, sonst aus den Einstellungen; leer = aus."""
    env = os.environ.get("SPOOLMAN_URL", "").strip()
    if env:
        return env.rstrip("/")
    settings = store.get_settings() if settings is None else settings
    return str(settings.get(URL_KEY) or "").strip().rstrip("/")


def from_env() -> bool:
    return bool(os.environ.get("SPOOLMAN_URL", "").strip())


def booking_since(settings: dict | None = None) -> float | None:
    settings = store.get_settings() if settings is None else settings
    since = settings.get(BOOK_KEY)
    return float(since) if since not in (None, "") and url(settings) else None


def _request(method: str, path: str, base: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read() or b"null")


def spools(settings: dict | None = None, force: bool = False) -> list[dict]:
    """Nicht archivierte Spulen, eine Minute zwischengespeichert; bei Fehlern die letzte Liste."""
    base = url(settings)
    if not base:
        return []
    with _lock:
        if not force and state["at"] and time.time() - state["at"] < CACHE_SECONDS and state.get("base") == base:
            return state["spools"]
    try:
        data = _request("GET", "/api/v1/spool", base)
        result = [s for s in data if isinstance(s, dict) and not s.get("archived")]
        with _lock:
            state.update(ok=True, error="", at=time.time(), spools=result, base=base)
        return result
    except (urllib.error.URLError, OSError, ValueError) as exc:
        with _lock:
            if state.get("ok") is not False:
                log.warning("Spoolman unter %s nicht erreichbar: %s", base, exc)
            state.update(ok=False, error=str(getattr(exc, "reason", exc)), at=time.time(), base=base)
            return state["spools"] if state.get("base") == base else []


# --- Zuordnung -----------------------------------------------------------------

def _hex(color: str | None) -> str:
    return (color or "").lstrip("#").upper()[:6]


def _material(text: str | None) -> str:
    return (text or "").strip().split(" ")[0].lower()


def candidates(fil: dict, all_spools: list[dict]) -> list[dict]:
    """Spulen mit gleicher Farbe und passendem Material: genau gleich, sonst gleiches Grundmaterial
    („PLA Basic“ in Spoolman passt zu „PLA“ aus der Datei)."""
    color = _hex(fil.get("color"))
    if not color:
        return []
    same_color = []
    for s in all_spools:
        f = s.get("filament") or {}
        colors = {_hex(f.get("color_hex"))} | {_hex(c) for c in (f.get("multi_color_hexes") or "").split(",") if c}
        if color in colors:
            same_color.append(s)
    wanted = (fil.get("type") or "").strip().lower()
    if not wanted:
        return same_color
    exact = [s for s in same_color if ((s.get("filament") or {}).get("material") or "").strip().lower() == wanted]
    return exact or [s for s in same_color if _material((s.get("filament") or {}).get("material")) == _material(wanted)]


def match(fil: dict, all_spools: list[dict]) -> list[dict] | None:
    """Spulen desselben Spoolman-Filaments; None, wenn nichts oder mehrere Filamente passen."""
    found = candidates(fil, all_spools)
    if not found or len({(s.get("filament") or {}).get("id") for s in found}) != 1:
        return None
    return found


def spool_price(spool: dict) -> float | None:
    """€/kg einer Spule: Spulenpreis durch Füllgewicht, sonst der Preis des Filaments."""
    f = spool.get("filament") or {}
    weight = spool.get("initial_weight") or f.get("weight")
    if spool.get("price") and weight:
        return spool["price"] / weight * 1000
    if f.get("price") and f.get("weight"):
        return f["price"] / f["weight"] * 1000
    return None


def price_for(fil: dict, all_spools: list[dict]) -> float | None:
    found = match(fil, all_spools)
    if not found:
        return None
    prices = [p for s in found if (p := spool_price(s)) is not None]
    return sum(prices) / len(prices) if prices else None


def price_resolver(settings: dict):
    """Preisquelle für store.filament_price, oder None ohne Spoolman."""
    if not url(settings):
        return None
    all_spools = spools(settings)
    return (lambda fil: price_for(fil, all_spools)) if all_spools else None


def label(spool: dict) -> str:
    f = spool.get("filament") or {}
    vendor = (f.get("vendor") or {}).get("name") or ""
    name = " ".join(x for x in (vendor, f.get("name") or f.get("material") or "") if x)
    return f"#{spool.get('id')} {name}".strip()


def for_filaments(filaments: list[dict], settings: dict | None = None) -> list[dict | None]:
    """Je Filament: passende Spulen mit Restmenge, oder None (nicht erreichbar/keine eindeutige)."""
    all_spools = spools(settings)
    out = []
    for fil in filaments:
        found = match(fil, all_spools) if all_spools else None
        if not found:
            out.append(None)
            continue
        found = sorted(found, key=lambda s: s.get("remaining_weight") or 0)
        remaining = sum(s.get("remaining_weight") or 0 for s in found)
        out.append({"spools": [{"id": s.get("id"), "label": label(s), "remaining": s.get("remaining_weight")}
                               for s in found],
                    "remaining": remaining, "enough": remaining >= (fil.get("used_g") or 0)})
    return out


# --- Stufe 2: Verbrauch melden --------------------------------------------------

def _job_filaments(con, job) -> list[dict]:
    """Filamente der gedruckten Platte (Schätzung des Slicers)."""
    rows = con.execute("""SELECT p.idx, p.filaments_json FROM plates p JOIN files f ON f.id = p.file_id
                          WHERE f.path = ? AND f.missing = 0 AND p.sliced = 1 ORDER BY p.idx""",
                       (job["local_path"],)).fetchall()
    if job["plate"] is not None:
        rows = [r for r in rows if r["idx"] == job["plate"]] or (rows if len(rows) == 1 else [])
    elif len(rows) != 1:
        return []
    return [f for f in json.loads(rows[0]["filaments_json"] or "[]") if (f.get("used_g") or 0) > 0] if rows else []


def use(spool_id: int, grams: float, base: str | None = None) -> None:
    _request("PUT", f"/api/v1/spool/{int(spool_id)}/use", base or url(), {"use_weight": round(grams, 2)})


def book_pending() -> int:
    """Fertige Drucke seit dem Einschalten buchen; mehrdeutige bleiben zur Auswahl offen."""
    settings = store.get_settings()
    since, base = booking_since(settings), url(settings)
    if since is None:
        return 0
    all_spools = spools(settings)
    if state.get("ok") is False:
        return 0
    booked = 0
    with _book_lock, store.connect() as con:
        jobs = con.execute("""SELECT * FROM print_jobs j WHERE status = 'completed' AND hidden = 0
                              AND local_path IS NOT NULL AND ended >= ?
                              AND NOT EXISTS (SELECT 1 FROM spoolman_bookings b WHERE b.job_id = j.id)""",
                           (since,)).fetchall()
        for job in jobs:
            for fil in _job_filaments(con, job):
                found = match(fil, all_spools)
                spool = found[0] if found and len(found) == 1 else None
                error = None
                if spool:
                    try:
                        use(spool["id"], fil["used_g"], base)
                        booked += 1
                    except (urllib.error.URLError, OSError, ValueError) as exc:
                        log.warning("Spoolman: Buchung für Druck %s fehlgeschlagen: %s", job["id"], exc)
                        return booked  # später erneut versuchen, nichts halb eintragen
                else:
                    error = "mehrdeutig" if found else "keine Spule"
                con.execute("""INSERT INTO spoolman_bookings (job_id, slot, spool_id, spool_label, grams, color,
                               material, booked, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (job["id"], fil.get("slot"), spool["id"] if spool else None,
                             label(spool) if spool else None, fil["used_g"], fil.get("color"), fil.get("type"),
                             time.time() if spool else None, error))
                con.commit()
    if booked:
        with _lock:
            state["at"] = 0  # Restmengen neu holen
    return booked


def bookings_for(job_ids: list[int]) -> dict[int, list[dict]]:
    if not job_ids:
        return {}
    with store.connect() as con:
        marks = ",".join("?" * len(job_ids))
        rows = con.execute(f"SELECT * FROM spoolman_bookings WHERE job_id IN ({marks}) ORDER BY slot", job_ids)
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r["job_id"], []).append(dict(r))
        return out


def book_manually(job_id: int, choices: dict[int, int]) -> None:
    """Offene Buchungen eines Drucks mit der gewählten Spule nachholen (Buchungs-ID -> Spulen-ID)."""
    base = url()
    known = {s.get("id"): s for s in spools(force=True)}
    with _book_lock, store.connect() as con:
        for booking_id, spool_id in choices.items():
            row = con.execute("SELECT * FROM spoolman_bookings WHERE id = ? AND job_id = ? AND booked IS NULL",
                              (booking_id, job_id)).fetchone()
            spool = known.get(spool_id)
            if not row or not spool:
                continue
            use(spool_id, row["grams"], base)
            con.execute("UPDATE spoolman_bookings SET spool_id = ?, spool_label = ?, booked = ?, note = NULL "
                        "WHERE id = ?", (spool_id, label(spool), time.time(), booking_id))
            con.commit()
    with _lock:
        state["at"] = 0
