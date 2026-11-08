#!/usr/bin/env python3
"""Baja los próximos partidos de River Plate desde la API pública de ESPN (sin clave) y los guarda en river.json.

Método: ESPN contesta bien cuando se le pide UN día (scoreboard?dates=AAAAMMDD) y da error con rangos.
Entonces revisamos día por día los próximos ~60 días y nos quedamos con los partidos de River.
Escribe en el log qué devolvió cada dirección, para diagnosticar rápido.
Si ninguna responde, o no hay partidos futuros, termina con error y NO pisa el river.json anterior.
Variables opcionales: RIVER_OUT (ruta de salida), ALLOW_EMPTY=1 (permite guardar la lista vacía, útil en receso).
"""
import json
import os
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

RIVER_ID = "16"  # River Plate en ESPN
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
AR = timezone(timedelta(hours=-3))  # Argentina no usa horario de verano
OUT = os.environ.get("RIVER_OUT", "river.json")
ALLOW_EMPTY = os.environ.get("ALLOW_EMPTY") == "1"
DAYS_AHEAD = 60
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


def league_of(ev, data, label):
    lg = ev.get("league")
    if isinstance(lg, dict) and lg.get("name"):
        return lg["name"]
    leagues = data.get("leagues") or []
    if len(leagues) == 1 and leagues[0].get("name"):
        return leagues[0]["name"]
    return label


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


def fetch_safe(url):
    try:
        return get_json(url), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def sweep(slug, label, days):
    """Revisa día por día el scoreboard de `slug`. Devuelve (partidos de River, días ok, días con error, eventos vistos, primer error)."""
    urls = [f"{BASE}/{slug}/scoreboard?dates={d}" for d in days]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fetch_safe, urls))
    mine, ok, bad, seen, first_err = [], 0, 0, 0, None
    for data, err in results:
        if err:
            bad += 1
            first_err = first_err or err
            continue
        ok += 1
        events = data.get("events") or []
        seen += len(events)
        for ev in events:
            m = to_match(ev, league_of(ev, data, label), trust=False)
            if m:
                mine.append(m)
    return mine, ok, bad, seen, first_err


def collect(now):
    today = now.astimezone(AR)
    days = [(today + timedelta(days=i)).strftime("%Y%m%d") for i in range(DAYS_AHEAD + 1)]
    found, responded = {}, 0

    def add(ms):
        for m in ms:
            found[m["id"] or (m["kickoff"], m["home"], m["away"])] = m

    # 1) Direcciones de "calendario del equipo" (solo para diagnóstico; ya vimos que a veces devuelven vacío)
    for name, url in (
        ("schedule arg.1 season", f"{BASE}/arg.1/teams/{RIVER_ID}/schedule?season={today.year}"),
        ("schedule arg.1 season fixture", f"{BASE}/arg.1/teams/{RIVER_ID}/schedule?season={today.year}&fixture=true"),
    ):
        data, err = fetch_safe(url)
        if err:
            print(f"[{name}] FALLÓ: {err}")
            continue
        responded += 1
        events = data.get("events") or []
        ms = [m for m in (to_match(ev, "Liga Profesional", True) for ev in events) if m]
        print(f"[{name}] OK: {len(events)} eventos, {len(ms)} de River sin terminar | {describe(events)}")
        add(ms)

    # 2) Día por día. Primero probamos "all" (todas las competencias en una sola dirección).
    probe, err = fetch_safe(f"{BASE}/all/scoreboard?dates={days[0]}")
    use_all = bool(probe) and len(probe.get("events") or []) > 5
    print(f"[scoreboard all hoy] {'OK: ' + str(len(probe.get('events') or [])) + ' eventos' if probe else 'FALLÓ: ' + str(err)}"
          f" -> {'usamos all' if use_all else 'usamos cada competencia por separado'}")
    targets = [("all", "")] if use_all else list(LEAGUES.items())
    for slug, label in targets:
        mine, ok, bad, seen, first_err = sweep(slug, label, days)
        if ok:
            responded += 1
        extra = f" | primer error: {first_err}" if first_err else ""
        print(f"[scoreboard {slug} x{len(days)} días] {ok} ok, {bad} con error, {seen} eventos vistos, "
              f"{len(mine)} de River sin terminar{extra}")
        add(mine)

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
    for m in matches[:3]:
        print("Próximo:", m["kickoff"], "|", m["home"], "vs", m["away"], "|", m["competition"],
              "| hora confirmada" if m["kickoff_confirmed"] else "| hora por confirmar")
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
