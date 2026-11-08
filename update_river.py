#!/usr/bin/env python3
"""Baja los próximos partidos de River Plate desde la API pública de ESPN (sin clave) y los guarda en river.json.

Prueba varias direcciones de ESPN y escribe en el log qué devolvió cada una, para diagnosticar rápido.
Si ninguna responde, o no hay partidos futuros, termina con error y NO pisa el river.json anterior.
Variables opcionales: RIVER_OUT (ruta de salida), ALLOW_EMPTY=1 (permite guardar la lista vacía, útil en receso).
"""
import json
import os
import urllib.request
from collections import Counter
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


def is_river(c):
    team = c.get("team") or {}
    return (str(team.get("id")) == RIVER_ID or str(c.get("id")) == RIVER_ID
            or "river plate" in (team.get("displayName") or "").lower())


def status_of(ev):
    comp = (ev.get("competitions") or [{}])[0]
    return (comp.get("status") or ev.get("status") or {}).get("type") or {}


def to_match(ev, league_name, trust):
    """Convierte un evento de ESPN a nuestro formato. `trust`=True si la dirección ya es solo de River."""
    comp = (ev.get("competitions") or [{}])[0]
    teams = comp.get("competitors") or []
    if not trust and not any(is_river(c) for c in teams):
        return None
    status = status_of(ev)
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
        "river_is_home": is_river(home),
        "competition": league_name,
    }


def describe(events):
    """Resumen para el log: rango de fechas y estados de los eventos."""
    dates = sorted(e["date"][:10] for e in events if e.get("date"))
    states = Counter(status_of(e).get("state") for e in events)
    rango = f"{dates[0]} -> {dates[-1]}" if dates else "sin fechas"
    return f"fechas {rango} | estados {dict(states)}"


def collect(now):
    d0 = now.astimezone(AR).strftime("%Y%m%d")
    d1 = (now + timedelta(days=75)).astimezone(AR).strftime("%Y%m%d")
    # (nombre, dirección, nombre de competencia, ¿la dirección es solo de River?)
    tries = []
    for slug, label in LEAGUES.items():
        tries.append((f"fixtures {slug}", f"{BASE}/{slug}/teams/{RIVER_ID}/schedule?fixture=true", label, True))
    tries.append(("fixtures all", f"{BASE}/all/teams/{RIVER_ID}/schedule?fixture=true", "", True))
    tries.append(("scoreboard arg.1 rango", f"{BASE}/arg.1/scoreboard?dates={d0}-{d1}", "Liga Profesional", False))
    tries.append(("scoreboard arg.1 hoy", f"{BASE}/arg.1/scoreboard?dates={d0}", "Liga Profesional", False))

    found, responded = {}, 0
    for name, url, label, trust in tries:
        try:
            data = get_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] FALLÓ: {e}")
            continue
        responded += 1
        league_name = ((data.get("leagues") or [{}])[0].get("name")) or label
        events = data.get("events") or []
        mine = [m for m in (to_match(ev, league_name, trust) for ev in events) if m]
        print(f"[{name}] OK: {len(events)} eventos, {len(mine)} de River sin terminar | {describe(events)}")
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
