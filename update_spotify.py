
import csv
import json
import os
import re
import time
from pathlib import Path
from difflib import SequenceMatcher

import spotipy
from spotipy.oauth2 import SpotifyOAuth
import requests
from spotipy.exceptions import SpotifyException

BASE = Path(__file__).resolve().parent
SONGS_FILE = BASE / "songs.csv"
CACHE_FILE = BASE / "spotify_track_cache.json"
NOT_FOUND_FILE = BASE / "not_found.csv"
DUPLICATES_FILE = BASE / "duplicate_tracks.csv"
REVIEW_FILE = BASE / "review_matches.csv"

PLAYLIST_ID = "2SZarSuNX3RgNSHUj1zVcQ"

# Eerst testen, zonder Spotify-playlist te wijzigen.
DRY_RUN = False

MIN_TRACKS = 2400
MAX_SEARCHES = 60

# Handmatig beoordeelde Spotify-versies.
OVERRIDES = {
    "Elton John - Cold Heart": "6zSpb8dQRaw0M1dK8PBwQz",
    "Mark Ronson - Valerie": "6kopmMZiyLmw7h66uXcXR7",
    "Amy Winehouse - Valerie": "6nLvaCZFR1wEzW3sIKpsnr",
}


def repair_text(value):
    """Herstel UTF-8-tekst die eerder als Windows-1252 is gelezen."""
    if not any(marker in value for marker in ("Ã", "Â", "â€")):
        return value
    try:
        repaired = value.encode("cp1252").decode("utf-8")
        return repaired if repaired else value
    except (UnicodeError, ValueError):
        return value


def read_songs():
    if not SONGS_FILE.exists():
        raise RuntimeError("songs.csv ontbreekt.")

    with SONGS_FILE.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)

        if "Track" not in (reader.fieldnames or []):
            raise RuntimeError(
                "Kolom Track ontbreekt in songs.csv."
            )

        return [
            repair_text(row["Track"].strip())
            for row in reader
            if row.get("Track", "").strip()
        ]


def read_cache():
    if not CACHE_FILE.exists():
        raise RuntimeError(
            "spotify_track_cache.json ontbreekt."
        )

    with CACHE_FILE.open(
        "r", encoding="utf-8"
    ) as file:
        cache = json.load(file)

    if not isinstance(cache, dict):
        raise RuntimeError(
            "Cache moet een JSON-object zijn."
        )

    return cache


def save_cache(cache):
    temp = CACHE_FILE.with_name(
        CACHE_FILE.name + ".tmp"
    )

    with temp.open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(
            cache,
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp, CACHE_FILE)


def save_report(path, columns, rows):
    with path.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as file:
        writer = csv.writer(file)
        writer.writerow(columns)
        writer.writerows(rows)


def spotify_client():
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
    refresh_token = os.environ.get("SPOTIFY_REFRESH_TOKEN")
    if not client_id or not client_secret:
        raise RuntimeError("Spotify Client ID of Secret ontbreekt.")
    if refresh_token:
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            auth=(client_id, client_secret),
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=30,
        )
        response.raise_for_status()
        access_token = response.json()["access_token"]
        return spotipy.Spotify(auth=access_token, requests_timeout=20, retries=0, status_retries=0)
    if os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("SPOTIFY_REFRESH_TOKEN ontbreekt in GitHub Secrets.")
    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri="http://127.0.0.1:3000",
        scope="playlist-modify-private playlist-modify-public playlist-read-private",
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth, requests_timeout=20, retries=0, status_retries=0)


def normalize(value):
    import unicodedata

    value = unicodedata.normalize(
        "NFKD",
        str(value).casefold()
    )

    value = "".join(
        ch for ch in value
        if not unicodedata.combining(ch)
    )

    value = value.replace("&", " and ")

    return " ".join(
        re.findall(r"[a-z0-9]+", value)
    )


def score_match(artist, title, item):
    actual_title = item.get("name", "")

    title_score = SequenceMatcher(
        None,
        normalize(title),
        normalize(actual_title)
    ).ratio()

    actual_artists = [
        normalize(a.get("name", ""))
        for a in item.get("artists", [])
    ]

    artist_score = max(
        (
            SequenceMatcher(
                None,
                normalize(artist),
                a
            ).ratio()
            for a in actual_artists
        ),
        default=0
    )

    if normalize(artist) in actual_artists:
        artist_score = 1.0

    version_words = (
        "acoustic",
        "live",
        "remix",
        "instrumental",
        "sped up",
        "slowed",
        "karaoke"
    )

    requested = normalize(title)
    actual = normalize(actual_title)

    unexpected_version = any(
        word in actual and word not in requested
        for word in version_words
    )

    return (
        title_score,
        artist_score,
        unexpected_version
    )


def search_song(sp, song):
    if " - " not in song:
        return None, None

    artist, title = song.split(" - ", 1)

    queries = [
        f'artist:"{artist}" track:"{title}"',
        f"{artist} {title}"
    ]

    candidates = {}

    for query in queries:
        results = sp.search(
            q=query,
            type="track",
            limit=10
        )

        for item in results.get(
            "tracks", {}
        ).get("items", []):
            if item.get("id"):
                candidates[item["id"]] = item

        time.sleep(0.2)

    ranked = sorted(
        (
            (
                score_match(
                    artist, title, item
                ),
                item
            )
            for item in candidates.values()
        ),
        key=lambda pair: (
            pair[0][2] is False,
            pair[0][0] * 0.7
            + pair[0][1] * 0.3
        ),
        reverse=True
    )

    if not ranked:
        return None, None

    (
        title_score,
        artist_score,
        unexpected_version
    ), best = ranked[0]

    suggestion = [
        song,
        best["id"],
        ", ".join(
            a["name"]
            for a in best["artists"]
        ),
        best["name"],
        f"{title_score:.3f}",
        f"{artist_score:.3f}",
        (
            "Ja"
            if unexpected_version
            else "Nee"
        )
    ]

    if (
        title_score >= 0.93
        and artist_score >= 0.90
        and not unexpected_version
    ):
        return best["id"], None

    return None, suggestion


def replace_playlist(sp, track_ids):
    sp.playlist_replace_items(
        PLAYLIST_ID,
        track_ids[:100]
    )

    print(
        "Playlist: 100/",
        len(track_ids)
    )

    for index in range(
        100,
        len(track_ids),
        100
    ):
        batch = track_ids[
            index:index + 100
        ]

        sp.playlist_add_items(
            PLAYLIST_ID,
            batch
        )

        print(
            "Playlist:",
            min(index + 100, len(track_ids)),
            "/",
            len(track_ids)
        )

        time.sleep(0.5)


def main():
    songs = read_songs()
    cache = read_cache()

    print(
        "\nSPOTIFY TOP 2500 - "
        "DEFINITIEVE UPDATER"
    )

    print("Kworb-regels:", len(songs))
    print("Cache-items:", len(cache))
    print("Testmodus:", DRY_RUN)

    if not (2300 <= len(songs) <= 2600):
        raise RuntimeError(
            "Onverwacht aantal Kworb-regels."
        )

    for song, track_id in OVERRIDES.items():
        if song in songs:
            cache[song] = track_id

            print(
                "Vaste match:",
                song,
                "->",
                track_id
            )

    save_cache(cache)

    sp = spotify_client()

    track_ids = []
    seen_ids = {}

    missing = []
    duplicates = []
    review = []

    new_matches = 0
    searches = 0
    search_limit_reached = False

    searched = {}

    for position, song in enumerate(
        songs, 1
    ):
        track_id = cache.get(song)

        if not (
            isinstance(track_id, str)
            and re.fullmatch(
                r"[A-Za-z0-9]{22}",
                track_id
            )
        ):
            track_id = None

        if not track_id:
            if song in searched:
                track_id, suggestion = (
                    searched[song]
                )

            elif searches >= MAX_SEARCHES:
                search_limit_reached = True

                missing.append([
                    position,
                    song,
                    "Zoeklimiet bereikt"
                ])

                continue

            else:
                searches += 1

                try:
                    track_id, suggestion = (
                        search_song(sp, song)
                    )

                except SpotifyException as exc:
                    save_cache(cache)

                    if exc.http_status == 429:
                        raise RuntimeError(
                            "Spotify-rate-limit: "
                            "veilig gestopt."
                        ) from exc

                    raise

                searched[song] = (
                    track_id,
                    suggestion
                )

                time.sleep(0.3)

            if track_id:
                cache[song] = track_id
                new_matches += 1

            else:
                missing.append([
                    position,
                    song,
                    "Geen betrouwbare match"
                ])

                if suggestion:
                    review.append(suggestion)

                continue

        if track_id in seen_ids:
            duplicates.append([
                position,
                song,
                track_id,
                seen_ids[track_id]
            ])

        else:
            seen_ids[track_id] = song
            track_ids.append(track_id)

        if position % 250 == 0:
            save_cache(cache)

            print(
                f"Verwerkt: {position}/"
                f"{len(songs)} | "
                f"Uniek: {len(track_ids)}"
            )

    save_cache(cache)

    save_report(
        NOT_FOUND_FILE,
        ["Rang", "Track", "Reden"],
        missing
    )

    save_report(
        DUPLICATES_FILE,
        [
            "Rang",
            "Track",
            "Spotify ID",
            "Eerste vermelding"
        ],
        duplicates
    )

    save_report(
        REVIEW_FILE,
        [
            "Kworb track",
            "Spotify ID",
            "Spotify artiesten",
            "Spotify titel",
            "Titelscore",
            "Artiestscore",
            "Onverwachte versie"
        ],
        review
    )

    print(
        "\n========== RESULTAAT =========="
    )

    print("Kworb:", len(songs))
    print(
        "Unieke Spotify-tracks:",
        len(track_ids)
    )
    print(
        "Nieuwe cachekoppelingen:",
        new_matches
    )
    print("Niet gevonden:", len(missing))
    print(
        "Dubbele Spotify-ID's:",
        len(duplicates)
    )
    print("Te controleren:", len(review))
    print("Zoekacties:", searches)
    print(
        "Zoeklimiet bereikt:",
        search_limit_reached
    )

    if DRY_RUN:
        print(
            "TEST AFGEROND - "
            "playlist NIET gewijzigd."
        )
        return

    if (
        len(track_ids) < MIN_TRACKS
        or missing
        or search_limit_reached
    ):
        raise RuntimeError(
            "Veiligheidscontrole mislukt: "
            "playlist NIET gewijzigd."
        )

    print(
        "Veiligheidscontrole geslaagd. "
        "Playlist wordt bijgewerkt..."
    )

    replace_playlist(
        sp,
        track_ids
    )

    print("PLAYLIST BIJGEWERKT")


if __name__ == "__main__":
    main()

