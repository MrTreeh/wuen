#!/usr/bin/env python3
"""
Taeglicher Kutscher-Update:
- Mitglieder per API einlesen, members.json abgleichen
  (neue hinzufuegen, ausgetretene entfernen, Counter bleibt erhalten)
- Kandidaten fuer heute bestimmen (Di/Sa: R4+R5, sonst: R3)
- Niedrigster Counter zuerst, Erstplatzierter bekommt +1
- data.json fuer die Website schreiben
"""

import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------- Konfig
API_URL = os.environ.get(
    "API_URL",
    "https://api.lastasylumtracker.com/v1/alliances/" + os.environ.get("ALLIANCE_ID", ""),
)
API_KEY = os.environ["API_KEY"]
MAX_AGE_SECONDS = 6 * 3600

MEMBERS_FILE = "members.json"
DATA_FILE = "data.json"
TZ = ZoneInfo("Europe/Berlin")

# Wochentage: Monday=0 ... Sunday=6
HIGH_RANK_DAYS = {1, 5}          # Dienstag, Samstag -> R4+R5
HIGH_RANKS = {4, 5}
NORMAL_RANKS = {3}               # alle anderen Tage -> R3


# ---------------------------------------------------------------- Helfer
def parse_rank(m):
    """Liest den Rang als Zahl aus rank_label ('R3', 'R4 · Overseer', ...)."""
    label = m.get("rank_label") or m.get("rank") or ""
    match = re.match(r"\s*R?(\d)", str(label))
    return int(match.group(1)) if match else 0


def load_state():
    """members.json laden. Fehlt die Datei, wird mit leerer Liste gestartet."""
    if not os.path.exists(MEMBERS_FILE):
        return []
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else []


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- API
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

    print("Top-Level-Keys:", list(payload.keys()), file=sys.stderr)

    age = payload.get("age_seconds")
    if age is not None and age > MAX_AGE_SECONDS:
        print(f"WARNUNG: Daten sind {age}s alt (cached={payload.get('cached')})",
              file=sys.stderr)

    members = payload.get("members")
    if members is None and isinstance(payload.get("data"), dict):
        members = payload["data"].get("members")
    if members is None:
        raise RuntimeError("Keine Mitgliederliste in der API-Antwort gefunden")

    print(f"Anzahl Mitglieder: {len(members)}", file=sys.stderr)
    if members:
        print("Beispiel-Mitglied:", json.dumps(members[0], ensure_ascii=False),
              file=sys.stderr)
    return members


# ---------------------------------------------------------------- Sync
def sync_members(state, api_members):
    """
    Gleicht members.json mit der API ab:
    - bestehende Mitglieder: Name/Rang aktualisieren, Counter behalten
    - neue Mitglieder: mit Counter 0 hinzufuegen
    - nicht mehr vorhandene: entfernen
    """
    old = {str(m["uid"]): m for m in state}
    new_state = []

    for m in api_members:
        uid = str(m.get("uid"))
        if not uid or uid == "None":
            continue
        prev = old.get(uid, {})
        new_state.append({
            "uid": uid,
            "nick_name": m.get("nick_name", prev.get("nick_name", "?")),
            "rank": parse_rank(m),
            "rank_label": m.get("rank_label", ""),
            "power": m.get("power", prev.get("power", 0)),
            "counter": prev.get("counter", 0),
            "last_driven": prev.get("last_driven"),
        })

    removed = set(old) - {m["uid"] for m in new_state}
    added = {m["uid"] for m in new_state} - set(old)
    print(f"Sync: {len(added)} neu, {len(removed)} entfernt, "
          f"{len(new_state)} gesamt", file=sys.stderr)
    return new_state


# ---------------------------------------------------------------- Auswahl
def candidates_for(state, today):
    """Kandidaten fuer den Tag, sortiert: niedrigster Counter zuerst,
    bei Gleichstand wer am laengsten nicht gefahren ist, dann Name."""
    allowed = HIGH_RANKS if today.weekday() in HIGH_RANK_DAYS else NORMAL_RANKS
    pool = [m for m in state if m["rank"] in allowed]
    pool.sort(key=lambda m: (
        m["counter"],
        m["last_driven"] or "",      # None/leer = noch nie gefahren -> zuerst
        m["nick_name"].lower(),
    ))
    return pool


# ---------------------------------------------------------------- Main
def main():
    now = datetime.now(TZ)
    today = now.date()
    today_str = today.isoformat()

    state = load_state()

    # Idempotenz: lief fuer heute schon ein Auswahl-Lauf, Counter nicht nochmal erhoehen
    already_done = False
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                last = json.load(f)
            already_done = last.get("date") == today_str
        except (json.JSONDecodeError, OSError):
            pass

    api_members = fetch_members()
    state = sync_members(state, api_members)

    pool = candidates_for(state, today)
    if not pool:
        raise RuntimeError("Keine Kandidaten fuer heute gefunden")

    top = pool[0]
    if not already_done:
        for m in state:
            if m["uid"] == top["uid"]:
                m["counter"] += 1
                m["last_driven"] = today_str
        # Pool nach dem +1 neu sortieren, damit die Anzeige stimmt
        pool = candidates_for(state, today)
    else:
        print("Fuer heute bereits gelaufen - Counter bleibt unveraendert",
              file=sys.stderr)

    data = {
        "date": today_str,
        "weekday": today.strftime("%A"),
        "updated_at": now.isoformat(timespec="seconds"),
        "allowed_ranks": sorted(
            HIGH_RANKS if today.weekday() in HIGH_RANK_DAYS else NORMAL_RANKS
        ),
        "driver": {
            "uid": top["uid"],
            "nick_name": top["nick_name"],
            "rank_label": top["rank_label"],
        },
        "candidates": [
            {
                "uid": m["uid"],
                "nick_name": m["nick_name"],
                "rank_label": m["rank_label"],
                "counter": m["counter"],
                "last_driven": m["last_driven"],
            }
            for m in pool
        ],
    }

    save_json(MEMBERS_FILE, state)
    save_json(DATA_FILE, data)
    print(f"Kutscher heute: {top['nick_name']} ({top['rank_label']})")


if __name__ == "__main__":
    main()
