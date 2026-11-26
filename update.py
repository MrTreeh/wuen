#!/usr/bin/env python3
"""
Taeglicher Kutscher-Update:
- Mitglieder per API einlesen, members.json abgleichen
  (neue hinzufuegen, ausgetretene entfernen, Counter bleibt erhalten)
- Kandidaten fuer heute bestimmen (Di/Sa: R4+R5, sonst: R3)
- Niedrigster Counter zuerst, Erstplatzierter bekommt +1
- Vorschau der naechsten 6 Tage simulieren (ohne echte Counter zu aendern)
- data.json fuer die Website schreiben

Testmodus: FORCE_RUN=1 -> Auswahl laeuft bei jedem Aufruf neu und zaehlt hoch.
"""

import copy
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------- Konfig
API_URL = os.environ.get(
    "API_URL",
    "https://api.lastasylumtracker.com/v1/alliances/" + os.environ.get("ALLY_ID", ""),
)
API_BASE = "https://api.lastasylumtracker.com"
API_KEY = os.environ["API_KEY"]
MAX_AGE_SECONDS = 6 * 3600

MEMBERS_FILE = "docs/members.json"
DATA_FILE = "docs/data.json"
TZ = ZoneInfo("Europe/Berlin")

PREVIEW_DAYS = 6
FORCE_RUN = os.environ.get("FORCE_RUN") == "1"

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
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def allowed_ranks_for(day):
    return HIGH_RANKS if day.weekday() in HIGH_RANK_DAYS else NORMAL_RANKS


def full_avatar(url):
    if not url:
        return ""
    if url.startswith("http"):
        return url
    return API_BASE + url


def driver_info(m):
    return {
        "uid": m["uid"],
        "nick_name": m["nick_name"],
        "rank_label": m["rank_label"],
        "power": m.get("power"),
        "avatar_url": full_avatar(m.get("avatar_url")),
    }


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
            "avatar_url": m.get("avatar_url") or prev.get("avatar_url", ""),
            "counter": prev.get("counter", 0),
            "last_driven": prev.get("last_driven"),
        })

    removed = set(old) - {m["uid"] for m in new_state}
    added = {m["uid"] for m in new_state} - set(old)
    print(f"Sync: {len(added)} neu, {len(removed)} entfernt, "
          f"{len(new_state)} gesamt", file=sys.stderr)
    return new_state


# ---------------------------------------------------------------- Auswahl
def sort_key(m):
    """Gemeinsame Sortierung fuer Auswahl und Statistik:
    niedrigster Counter zuerst, bei Gleichstand wer am laengsten nicht
    gefahren ist (noch nie = zuerst), dann Name."""
    return (
        m["counter"],
        m["last_driven"] or "",
        m["nick_name"].lower(),
    )


def candidates_for(state, day):
    """Kandidaten fuer den Tag (nur erlaubte Raenge), sortiert via sort_key."""
    allowed = allowed_ranks_for(day)
    pool = [m for m in state if m["rank"] in allowed]
    pool.sort(key=sort_key)
    return pool


def build_preview(state, today, days=PREVIEW_DAYS):
    """Simuliert die naechsten `days` Tage auf einer Kopie des Zustands.
    Die echten Counter bleiben unveraendert."""
    sim = copy.deepcopy(state)
    preview = []
    for i in range(1, days + 1):
        day = today + timedelta(days=i)
        pool = candidates_for(sim, day)
        entry = {
            "date": day.isoformat(),
            "weekday": day.strftime("%A"),
            "allowed_ranks": sorted(allowed_ranks_for(day)),
            "driver": None,
        }
        if pool:
            pick = pool[0]
            entry["driver"] = driver_info(pick)
            # nur in der Simulation
            pick["counter"] += 1
            pick["last_driven"] = day.isoformat()
        preview.append(entry)
    return preview


# ---------------------------------------------------------------- Main
def main():
    now = datetime.now(TZ)
    today = now.date()
    today_str = today.isoformat()

    state = load_state()

    # Idempotenz: Der Tag gilt nur als erledigt, wenn members.json wirklich
    # einen Fahrer von heute enthaelt (nicht data.json!).
    already_done = any(m.get("last_driven") == today_str for m in state)

    if FORCE_RUN:
        print("TESTMODUS: FORCE_RUN=1, Idempotenz deaktiviert", file=sys.stderr)
        already_done = False

    api_members = fetch_members()
    state = sync_members(state, api_members)

    # Nach dem Sync nochmal pruefen (falls der heutige Fahrer ausgetreten ist)
    if not FORCE_RUN:
        already_done = any(m.get("last_driven") == today_str for m in state)

    print(f"already_done={already_done}, "
          f"Kandidaten={len(candidates_for(state, today))}", file=sys.stderr)

    if already_done:
        print("Fuer heute bereits gelaufen - Counter bleibt unveraendert",
              file=sys.stderr)
        todays = [m for m in state if m["last_driven"] == today_str]
        pool = candidates_for(state, today)
        if not pool:
            raise RuntimeError("Keine Kandidaten fuer heute gefunden")
        top = todays[0] if todays else pool[0]
    else:
        pool = candidates_for(state, today)
        if not pool:
            raise RuntimeError("Keine Kandidaten fuer heute gefunden")
        top = pool[0]
        for m in state:
            if m["uid"] == top["uid"]:
                m["counter"] += 1
                m["last_driven"] = today_str
        print(f"Counter erhoeht: {top['nick_name']} -> {top['counter']}",
              file=sys.stderr)

    # Vorschau auf Basis des Zustands NACH der heutigen Auswahl
    preview = build_preview(state, today)

    # Statistik: alle Mitglieder, unabhaengig vom heutigen Tag
    STATS_RANKS = {3, 4, 5}
    stats_pool = [m for m in state if m["rank"] in STATS_RANKS]
    all_sorted = sorted(stats_pool, key=sort_key)
    allowed_today = allowed_ranks_for(today)

    data = {
        "date": today_str,
        "weekday": today.strftime("%A"),
        "updated_at": now.isoformat(timespec="seconds"),
        "allowed_ranks": sorted(allowed_today),
        "driver": driver_info(top),
        "candidates": [
            {
                "uid": m["uid"],
                "nick_name": m["nick_name"],
                "rank_label": m["rank_label"],
                "counter": m["counter"],
                "last_driven": m["last_driven"],
                "avatar_url": full_avatar(m.get("avatar_url")),
                "eligible_today": m["rank"] in allowed_today,
            }
            for m in all_sorted
        ],
        "preview": preview,
    }

    save_json(MEMBERS_FILE, state)
    save_json(DATA_FILE, data)
    print(f"Kutscher heute: {top['nick_name']} ({top['rank_label']})")


if __name__ == "__main__":
    main()
