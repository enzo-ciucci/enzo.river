#!/usr/bin/env python3
"""Baja los próximos partidos de River Plate desde API-Football y los guarda en river.json.

Necesita la variable de entorno API_FOOTBALL_KEY.
Opcional: RIVER_TEAM_ID (para saltear la búsqueda) y RIVER_OUT (ruta de salida).
Si algo falla, termina con error y NO pisa el river.json anterior.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://v3.football.api-sports.io"
TZ = "America/Argentina/Buenos_Aires"
OUT = os.environ.get("RIVER_OUT", "river.json")
KEY = os.environ.get("API_FOOTBALL_KEY", "")

# Estados de API-Football: TBD = hora a definir, PST = postergado
SIN_HORA_CONFIRMADA = {"TBD", "PST"}
DESCARTAR = {"CANC", "ABD", "AWD", "WO", "FT", "AET", "PEN"}


def get(path, **params):
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    errors = data.get("errors")
    if errors:  # la API responde 200 aunque haya errores (lista o dict)
        raise RuntimeError(f"{path}: {errors}")
    return data.get("response", [])


def find_team_id():
    forced = os.environ.get("RIVER_TEAM_ID")
    if forced:
        return int(forced), "River Plate"
    for item in get("teams", search="river"):
        t = item["team"]
        if t.get("country") == "Argentina" and t.get("name", "").lower() == "river plate":
            return t["id"], t["name"]
    raise RuntimeError("No encontré a River Plate (Argentina) en la búsqueda")


def fetch_fixtures(team_id, now):
    # Temporada = año calendario. Desde noviembre miramos también el año siguiente.
    seasons = [now.year] + ([now.year + 1] if now.month >= 11 else [])
    rows, errs = [], []
    for s in seasons:
        try:
            rows += get("fixtures", team=team_id, season=s, timezone=TZ)
        except Exception as e:  # noqa: BLE001
            errs.append(str(e))
            print("AVISO:", e, file=sys.stderr)
    if not rows and errs:
        raise RuntimeError("; ".join(errs))
    return rows


def build_matches(rows, team_id, now):
    out = []
    for r in rows:
        f = r["fixture"]
        status = f["status"]["short"]
        if status in DESCARTAR:
            continue
        start = datetime.fromisoformat(f["date"])
        if start < now - timedelta(hours=3):  # ya pasó (dejamos 3 h por los partidos en juego)
            continue
        out.append({
            "kickoff": f["date"],
            "kickoff_confirmed": status not in SIN_HORA_CONFIRMADA,
            "status": status,
            "home": r["teams"]["home"]["name"],
            "away": r["teams"]["away"]["name"],
            "river_is_home": r["teams"]["home"]["id"] == team_id,
            "competition": r["league"]["name"],
        })
    out.sort(key=lambda m: datetime.fromisoformat(m["kickoff"]))
    return out[:15]


def main():
    if not KEY:
        sys.exit("Falta la variable API_FOOTBALL_KEY")
    now = datetime.now(timezone.utc)
    team_id, team_name = find_team_id()
    matches = build_matches(fetch_fixtures(team_id, now), team_id, now)
    payload = {
        # "updated" cambia en cada corrida: así siempre hay commit y GitHub no pausa la tarea programada
        "updated": now.isoformat(timespec="seconds"),
        "team": {"id": team_id, "name": team_name},
        "matches": matches,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"OK: {len(matches)} partidos guardados en {OUT}")


if __name__ == "__main__":
    main()
