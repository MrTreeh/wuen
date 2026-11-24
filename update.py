import json
import os
from datetime import date
from pathlib import Path

import requests

BASE_URL = os.environ["BASE_URL"]      # das, was in PowerShell $baseUrl ist
ALLY_ID = os.environ["ALLY_ID"]        # $allyId
API_KEY = os.environ["API_KEY"]
DATA_FILE = Path("docs/members.json")

FIELDS = ["uid", "nick_name", "rank_label", "power", "online",
          "last_online_at", "joined_at", "avatar_url"]


def fetch_members():
    url = f"{BASE_URL}/alliances/{ALLY_ID}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PowerShell/7",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    ally = r.json()
    return [{k: m.get(k) for k in FIELDS} for m in ally["members"]]


def main():
    members = fetch_members()
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(
            {"updated": date.today().isoformat(), "members": members},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"{len(members)} Mitglieder gespeichert.")


if __name__ == "__main__":
    main
