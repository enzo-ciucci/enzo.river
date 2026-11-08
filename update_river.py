#!/usr/bin/env python3
"""Baja los próximos partidos de River Plate desde la API pública de ESPN (sin clave) y los guarda en river.json.

Prueba varias direcciones de ESPN y escribe en el log cuál funcionó, para poder diagnosticar rápido.
Si ninguna responde, o no hay partidos futuros, termina con error y NO pisa el river.json anterior.
Variables opcionales: RIVER_OUT (ruta de salida), ALLOW_EMPTY=1 (permite guardar la lista vacía, útil en receso).
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

RIVER_ID = "16"  # River Plate en ESPN
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
AR = timezone(timedelta(hours=-3))  # Argentina no usa horario de verano
OUT = os.environ.get("RIVER_OUT", "river.json")
ALLOW_EMPTY = os.environ.get("ALLOW_EMPTY") == "1"
LEAGUES = {
    "arg.1": "Liga Profesional",
    "arg.copa": "Copa Argentina",
    "conmebol.sudamericana": "Copa Sudamericana",
    "conmebol.libertadores": "Copa Libertadores",
}


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; enzo-river/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def parse_date(s):
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M%z")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(AR)


def to_match(ev, league_name):
    """Convierte un evento de ESPN en nuestro formato. Devuelve None si no es de River o ya terminó."""
    comp = (ev.get("competitions") or [{}])[0]
    teams = comp.get("competitors") or []
    if RIVER_ID not in [str((c.get("team") or {}).get("id")) for c in teams]:
        return None
    status = (comp.get("status") or ev.get("status") or {}).get("type") or {}
    name = (status.get("name") or "").upper()
    if status.get("state") == "post" or status.get("completed") or "CANCEL" in name:
        return None
    raw = ev.get("date") or comp.get("date")
    if not raw:
        return None
    start = parse_date(raw)
    home = next((c for c in teams if c.get("homeAway") == "home"), {})
    away = next((c for c in teams if c.get("homeAway") == "away"), {})
    confirmed = comp.get("timeValid", True) is not False and "POSTPON" not in name and "TBD" not in name
    return {
        "id": str(ev.get("id") or ""),
        "kickoff": start.isoformat(timespec="seconds"),
        "kickoff_confirmed": confirmed,
        "status": status.get("description") or status.get("name") or "",
        "home": (home.get("team") or {}).get("displayName", ""),
        "away": (away.get("team") or {}).get("displayName", ""),
        "river_is_home": str((home.get("team") or {}).get("id")) == RIVER_ID,
        "competition": league_name,
    }


def collect(now):
    d0 = now.astimezone(AR).strftime("%Y%m%d")
    d1 = (now + timedelta(days=75)).astimezone(AR).strftime("%Y%m%d")
    tries = [
        ("schedule arg.1", f"{BASE}/arg.1/teams/{RIVER_ID}/schedule", "Liga Profesional"),
        ("schedule all", f"{BASE}/all/teams/{RIVER_ID}/schedule", ""),
    ]
    for slug, label in LEAGUES.items():
        tries.append((f"scoreboard {slug}", f"{BASE}/{slug}/scoreboard?dates={d0}-{d1}&limit=500", label))

    found, responded = {}, 0
    for name, url, label in tries:
        try:
            data = get_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] FALLÓ: {e}")
            continue
        responded += 1
        league_name = ((data.get("leagues") or [{}])[0].get("name")) or label
        events = data.get("events") or []
        mine = [m for m in (to_match(ev, league_name) for ev in events) if m]
        print(f"[{name}] OK: {len(events)} eventos, {len(mine)} de River sin terminar")
        for m in mine:
            found[m["id"] or (m["kickoff"], m["home"], m["away"])] = m
    if responded == 0:
        raise RuntimeError("Ninguna dirección de ESPN respondió")
    return list(found.values())


def main():
    now = datetime.now(timezone.utc)
    matches = [m for m in collect(now) if datetime.fromisoformat(m["kickoff"]) >= now - timedelta(hours=3)]
    matches.sort(key=lambda m: datetime.fromisoformat(m["kickoff"]))
    matches = matches[:15]
    if not matches and not ALLOW_EMPTY:
        raise RuntimeError("ESPN respondió pero no devolvió partidos futuros de River; revisá el log")
    for m in matches:
        m.pop("id", None)
    if matches:
        print("Próximo partido:", matches[0])
    payload = {
        # "updated" cambia en cada corrida: así siempre hay commit y GitHub no pausa la tarea programada
        "updated": now.isoformat(timespec="seconds"),
        "team": {"id": RIVER_ID, "name": "River Plate"},
        "matches": matches,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"OK: {len(matches)} partidos guardados en {OUT}")


if __name__ == "__main__":
    main()
