import csv
import requests
from bs4 import BeautifulSoup

URL = "https://kworb.net/spotify/songs.html"
response = requests.get(URL, timeout=45, headers={"User-Agent": "Mozilla/5.0"})
response.raise_for_status()
soup = BeautifulSoup(response.text, "html.parser")
table = soup.find("table")
if table is None:
    raise RuntimeError("Geen Kworb-tabel gevonden")
headers = [th.get_text(" ", strip=True) for th in table.select("tr th")]
if not any("artist" in h.lower() or "track" in h.lower() for h in headers):
    raise RuntimeError("Kworb-kolommen zijn gewijzigd; update afgebroken")
TOP_TRACKS = 2500
tracks = []
for row in table.select("tr"):
    cells = row.find_all("td")
    if not cells:
        continue
    song = cells[0].get_text(" ", strip=True)
    tracks.append(song)
    if len(tracks) == TOP_TRACKS:
        break
if len(tracks) != TOP_TRACKS or not all(tracks):
    raise RuntimeError(f"Kworb leverde geen volledige top {TOP_TRACKS}. CSV niet vervangen.")
with open("songs.csv", "w", encoding="utf-8-sig", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["Track"])
    writer.writerows([[track] for track in tracks])
print(f"Kworb bijgewerkt: {len(tracks)} tracks")
