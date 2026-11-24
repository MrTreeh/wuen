import requests
import json
import os

# 1. Konfiguration
base_url = os.environ["BASE_URL"]       # anpassen
ally_id = os.environ["ALLY_ID"]             # anpassen
token = os.environ["API_KEY"]         # kommt aus dem GitHub Secret

headers = {
    "Authorization": f"Bearer {token}"
}

# 2. API abfragen (hier kommt dein Block hin)
resp = requests.get(f"{base_url}/alliances/{ally_id}", headers=headers)
print("Status:", resp.status_code)
print("Antwort (Anfang):", resp.text[:300])
resp.raise_for_status()

ally = resp.json()
members = ally.get("members", [])
print("Anzahl Members:", len(members))

# 3. Datei schreiben
os.makedirs("docs", exist_ok=True)
with open("docs/members.json", "w", encoding="utf-8") as f:
    json.dump(members, f, ensure_ascii=False, indent=2)
print("Geschrieben:", len(members), "Members")
