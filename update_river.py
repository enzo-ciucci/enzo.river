#!/usr/bin/env python3
"""Baja los próximos partidos de River Plate desde TheSportsDB (gratis, sin clave) y los guarda en river.json.

Si algo falla, termina con error y NO pisa el river.json anterior.
Opcional: RIVER_OUT (ruta de salida).
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

TEAM_ID = "135171"  # River Plate en TheSportsDB
BASE = "https://www.thesportsdb.com/api/v1/json"
KEYS = ("123", "3")  # claves públicas de prueba que ofrece TheSportsDB
AR = timezone(timedelta(hours=-3))  # Argentina no usa horario de verano
OUT = os.environ.get("RIVER_OUT", "river.json")

DESCARTAR = {"match finished", "ft", "aet", "pen", "canceled", "cancelled", "abandoned", "walkover"}
SIN_HORA = {"postponed", "pst", "tbd", "time to be defined"}


def fetch_events():
    """Devuelve la lista de próximos eventos (puede ser vacía). Falla solo si ninguna clave respondió."""
    errors = []
    for key in KEYS:
        url = f"{BASE}/{key}/eventsnext.php?id={TEAM_ID}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "enzo-river/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            events = data.get("events") or []
            if events:
                return events
            errors.append(f"clave {key}: respuesta sin eventos")
        except Exception as e:  # noqa: BLE001
            errors.append(f"clave {key}: {e}")
            print("AVISO:", errors[-1], file=sys.stderr)
    if all("sin eventos" in e for e in errors):
        return []  # respuesta válida pero vacía (por ejemplo, receso)
    raise RuntimeError("; ".join(errors))


def parse_start(e):
    """Devuelve (inicio con zona horaria, hora_confirmada). TheSportsDB informa en UTC."""
    ts = e.get("strTimestamp")
    if ts:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(AR), True
    date = e.get("dateEvent")
    if date:  # hay fecha pero no hora
        return datetime.fromisoformat(date).replace(tzinfo=AR), False
    return None, False


def build_matches(events, now):
    out = []
    for e in events:
        status = (e.get("strStatus") or "").strip().lower()
        if status in DESCARTAR:
            continue
        start, confirmed = parse_start(e)
        if start is None or start < now - timedelta(hours=3):
            continue
        out.append({
            "kickoff": start.isoformat(timespec="seconds"),
            "kickoff_confirmed": confirmed and status not in SIN_HORA,
            "status": e.get("strStatus") or "",
            "home": e.get("strHomeTeam") or "",
            "away": e.get("strAwayTeam") or "",
            "river_is_home": str(e.get("idHomeTeam")) == TEAM_ID,
            "competition": e.get("strLeague") or "",
        })
    out.sort(key=lambda m: datetime.fromisoformat(m["kickoff"]))
    return out[:15]


def main():
    now = datetime.now(timezone.utc)
    events = fetch_events()
    if events:  # para chequear a ojo que la hora sea la correcta
        e = events[0]
        print("Primer evento crudo:", {k: e.get(k) for k in (
            "strEvent", "dateEvent", "strTime", "strTimestamp", "strStatus", "strLeague")})
    matches = build_matches(events, now)
    payload = {
        # "updated" cambia en cada corrida: así siempre hay commit y GitHub no pausa la tarea programada
        "updated": now.isoformat(timespec="seconds"),
        "team": {"id": TEAM_ID, "name": "River Plate"},
        "matches": matches,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"OK: {len(matches)} partidos guardados en {OUT}")


if __name__ == "__main__":
    main()
