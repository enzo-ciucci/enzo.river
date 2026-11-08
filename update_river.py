#!/usr/bin/env python3
"""Baja los próximos partidos de River Plate desde la API pública de ESPN (sin clave) y los guarda en river.json.

Fuente principal: el calendario de la temporada de River en cada competencia (teams/16/schedule?season=AAAA).
Red de seguridad: si ahí no hay partidos por jugar, revisa día por día los próximos ~60 días
(ESPN contesta bien un día solo y da error con rangos de fechas).

Si no hay partidos definidos, guarda "matches": [] (es un resultado válido, no un error).
Falla (y NO pisa el river.json anterior) solo si ESPN no responde o responde sin ningún dato.
Variable opcional: RIVER_OUT (ruta de salida).
"""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

RIVER_ID = "16"  # River Plate en ESPN
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
AR = timezone(timedelta(hours=-3))  # Argentina no usa horario de verano
OUT = os.environ.get("RIVER_OUT", "river.json")
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


def fetch_safe(url):
    try:
        return get_json(url), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def fetch_many(urls):
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(fetch_safe, urls))


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


def collect(now):
    today = now.astimezone(AR)
    found, responded, seen = {}, 0, 0

    def add(ms):
        for m in ms:
            found.setdefault(m["id"] or (m["kickoff"], m["home"], m["away"]), m)

    # 1) Calendario de River por competencia y temporada (desde noviembre miramos también el año siguiente)
    seasons = [today.year] + ([today.year + 1] if today.month >= 11 else [])
    tasks = [(slug, label, s) for s in seasons for slug, label in LEAGUES.items()]
    urls = [f"{BASE}/{slug}/teams/{RIVER_ID}/schedule?season={s}" for slug, _, s in tasks]
    for (slug, label, s), (data, err) in zip(tasks, fetch_many(urls)):
        if err:
            print(f"[calendario {slug} {s}] sin datos: {err}")
            continue
        responded += 1
        events = data.get("events") or []
        seen += len(events)
        ms = [m for m in (to_match(ev, label, True) for ev in events) if m]
        print(f"[calendario {slug} {s}] {len(events)} partidos de River en la temporada, {len(ms)} por jugar")
        add(ms)

    # 2) Red de seguridad: si no hay partidos por jugar, miramos día por día
    if not found:
        days = [(today + timedelta(days=i)).strftime("%Y%m%d") for i in range(DAYS_AHEAD + 1)]
        for slug, label in LEAGUES.items():
            results = fetch_many([f"{BASE}/{slug}/scoreboard?dates={d}" for d in days])
            ok = sum(1 for _, err in results if not err)
            vistos, mine = 0, []
            for data, err in results:
                if err:
                    continue
                events = data.get("events") or []
                vistos += len(events)
                mine += [m for m in (to_match(ev, label, False) for ev in events) if m]
            first_err = next((err for _, err in results if err), None)
            if ok:
                responded += 1
            seen += vistos
            extra = f" | primer error: {first_err}" if first_err else ""
            print(f"[día por día {slug}] {ok}/{len(days)} días ok, {vistos} partidos vistos, {len(mine)} de River por jugar{extra}")
            add(mine)

    if responded == 0:
        raise RuntimeError("Ninguna dirección de ESPN respondió")
    if seen == 0:
        raise RuntimeError("ESPN respondió pero sin ningún partido: algo cambió en la fuente; revisá el log")
    return list(found.values())


def main():
    now = datetime.now(timezone.utc)
    matches = [m for m in collect(now) if datetime.fromisoformat(m["kickoff"]) >= now - timedelta(hours=3)]
    matches.sort(key=lambda m: datetime.fromisoformat(m["kickoff"]))
    matches = matches[:15]
    for m in matches:
        m.pop("id", None)
    if matches:
        for m in matches[:3]:
            print("Próximo:", m["kickoff"], "|", m["home"], "vs", m["away"], "|", m["competition"],
                  "| hora confirmada" if m["kickoff_confirmed"] else "| hora por confirmar")
    else:
        print("River no tiene partidos definidos por ahora (se guarda la lista vacía).")
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
