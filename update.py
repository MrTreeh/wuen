#!/usr/bin/env python3
"""Kutscher-Auswahl: liest Allianz-Mitglieder, bestimmt den Fahrer des Tages
und schreibt members.json + data.json fuer die Webseite."""

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

API_KEY = os.environ["API_KEY"]
ALLY_ID = os.environ["ALLY_ID"]
API_URL = f"https://api.lastasylumtracker.com/v1/alliances/{ALLY_ID}"

MEMBERS_FILE = Path("members.json")
DATA_FILE = Path("data.json")

MIN_ACTIVE = 1            # active >= MIN_ACTIVE gilt als aktiv
PREVIEW_DAYS = 6
MAX_AGE_SECONDS = 3600    # aeltere Daten -> Warnung

# Wochentag (Mo=0 ... So=6): Dienstag (1) und Samstag (5) = Rang 4/5
HIGH_RANK_DAYS = {1, 5}
HIGH_RANKS = {4, 5}
LOW_RANKS = {3}


def fetch_members():
    resp = requests.get(
        API_URL,
        params={"fresh": 1},
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "User-Agent": "Mozilla/5.0 (compatible; KutscherBot/1.0)",
            "Accept": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()

    age = payload.get("age_seconds")
    if age is not None and age > MAX_AGE_SECONDS:
        print(f"WARNUNG: Daten sind {age}s alt (cached={payload.get('cached')})",
              file=sys.stderr)

    # Mitgliederliste: je nach Antwortformat in "members" oder direkt
    members = payload.get("members")
    if members is None and isinstance(payload.get("data"), dict):
        members = payload["data"].get("members")
    if members is None:
        raise RuntimeError("Keine Mitgliederliste in der API-Antwort gefunden")
    return members


def load_state():
    if MEMBERS_FILE.exists():
        return json.loads(MEMBERS_FILE.read_text(encoding="utf-8"))
    return {}


def sync_members(api_members, state, today):
    """Gleicht members.json mit der API ab: neue hinzufuegen, ausgetretene
    entfernen, Name/Rang/Power/active aktualisieren."""
    new_state = {}
    for m in api_members:
        uid = str(m.get("uid") or m.get("id"))
        name = m.get("name") or m.get("nick_name") or uid
        old = state.get(uid, {})
        new_state[uid] = {
            "uid": uid,
            "name": name,
            "rank": int(m.get("rank", 0)),
            "power": m.get("power", 0),
            "active": m.get("active", 0) or 0,
            # Neue Mitglieder: heutiges Datum -> hinten einsortiert
            "last_driven": old.get("last_driven", today.isoformat()),
            "counter": old.get("counter", 0),
        }
    return new_state


def eligible_ranks(day):
    return HIGH_RANKS if day.weekday() in HIGH_RANK_DAYS else LOW_RANKS


def candidates(state, day, drove_on=None):
    """Sortierte Kandidatenliste fuer einen Tag.
    drove_on: dict uid -> datum, fuer die Vorschau (simulierte Fahrten)."""
    ranks = eligible_ranks(day)
    pool = [m for m in state.values() if m["rank"] in ranks]
    active_pool = [m for m in pool if m["active"] >= MIN_ACTIVE]

    fallback = False
    if not active_pool:
        active_pool = pool
        fallback = True

    def key(m):
        last = (drove_on or {}).get(m["uid"], m["last_driven"])
        return (last, m["uid"])

    return sorted(active_pool, key=key), fallback


def build_preview(state, start_day):
    """Naechste PREVIEW_DAYS Fahrer, ohne Counter/last_driven zu veraendern."""
    simulated = {}
    preview = []
    for i in range(1, PREVIEW_DAYS + 1):
        day = start_day + timedelta(days=i)
        cands, fallback = candidates(state, day, simulated)
        if not cands:
            continue
        pick = cands[0]
        simulated[pick["uid"]] = day.isoformat()
        preview.append({
            "date": day.isoformat(),
            "weekday": day.weekday(),
            "uid": pick["uid"],
            "name": pick["name"],
            "rank": pick["rank"],
            "power": pick["power"],
            "fallback": fallback,
        })
    return preview


def main():
    today = date.today()
    state = load_state()
    api_members = fetch_members()
    state = sync_members(api_members, state, today)

    cands, fallback = candidates(state, today)
    if not cands:
        raise RuntimeError("Keine Kandidaten fuer heute gefunden")

    driver = cands[0]
    # Nur einmal pro Tag buchen (Mehrfachlaeufe am selben Tag)
    already = DATA_FILE.exists() and \
        json.loads(DATA_FILE.read_text(encoding="utf-8")).get("date") == today.isoformat()
    if already:
        prev = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        driver = state.get(prev["today"]["uid"], driver)
    else:
        state[driver["uid"]]["last_driven"] = today.isoformat()
        state[driver["uid"]]["counter"] += 1

    # Vorschau nach der Buchung berechnen
    preview = build_preview(state, today)

    MEMBERS_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    data = {
        "date": today.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "today": {
            "uid": driver["uid"],
            "name": driver["name"],
            "rank": driver["rank"],
            "power": driver["power"],
            "fallback": fallback,
        },
        "preview": preview,
        "stats": sorted(
            ({"name": m["name"], "rank": m["rank"], "counter": m["counter"],
              "last_driven": m["last_driven"]} for m in state.values()),
            key=lambda x: (-x["counter"], x["name"]),
        ),
    }
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Kutscher heute: {driver['name']} (R{driver['rank']})")


if __name__ == "__main__":
    main()
