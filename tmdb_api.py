# ============================================================
# WATCHKARO - TMDB API HELPER
# ============================================================
#
# Restored project-compatible TMDB helper.
#
# Uses:
#   - TMDB_ACCESS_TOKEN from .env when available
#   - TMDB_API_KEY from .env as fallback
#
# Public helpers used by WatchKaro:
#   search_movies()
#   get_movie_recommendations()
#   get_movie_details()
#   get_movie_credits()
#   get_person_movie_credits()
#   get_poster_url()
#   get_2026_movies()
# ============================================================

import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
TMDB_ACCESS_TOKEN = os.getenv("TMDB_ACCESS_TOKEN", "").strip()

if TMDB_ACCESS_TOKEN:
    TMDB_HEADERS = {
        "Authorization": f"Bearer {TMDB_ACCESS_TOKEN}",
        "accept": "application/json",
    }
else:
    TMDB_HEADERS = {
        "accept": "application/json",
    }


def _request(path, params=None, timeout=15):
    """Send a safe GET request to TMDB."""
    params = dict(params or {})

    # API-key auth is supported as a fallback when no Bearer token exists.
    if not TMDB_ACCESS_TOKEN and TMDB_API_KEY:
        params["api_key"] = TMDB_API_KEY

    response = requests.get(
        f"{TMDB_BASE_URL}{path}",
        headers=TMDB_HEADERS,
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def search_movies(query, page=1):
    """
    Search TMDB for both movies and TV series.

    The WatchKaro frontend uses media_type to distinguish movies and TV.
    """
    query = str(query or "").strip()
    if not query:
        return []

    data = _request(
        "/search/multi",
        params={
            "query": query,
            "language": "en-US",
            "include_adult": "false",
            "page": int(page),
        },
    )

    results = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue

        media_type = item.get("media_type")
        if media_type not in {"movie", "tv"}:
            continue

        cleaned = dict(item)

        # Normalize names so the rest of the project can consistently
        # display a "title" field.
        if media_type == "tv" and not cleaned.get("title"):
            cleaned["title"] = cleaned.get("name", "")

        cleaned["tmdb_id"] = cleaned.get("id")
        results.append(cleaned)

    return results


def get_movie_details(movie_id):
    """
    Get complete TMDB movie details plus keywords and credits.
    """
    if not movie_id:
        return {}

    return _request(
        f"/movie/{int(movie_id)}",
        params={
            "language": "en-US",
            "append_to_response": "keywords,credits",
        },
    )


def get_movie_credits(movie_id):
    """Return cast and crew credits for a TMDB movie."""
    if not movie_id:
        return {}

    return _request(
        f"/movie/{int(movie_id)}/credits",
        params={
            "language": "en-US",
        },
    )


def get_person_movie_credits(person_id, role="cast"):
    """Return movie filmography entries for an actor or director."""
    if not person_id:
        return []

    data = _request(
        f"/person/{int(person_id)}/combined_credits",
        params={
            "language": "en-US",
        },
    )

    role = str(role or "cast").lower().strip()
    source = data.get("cast", []) if role == "cast" else data.get("crew", [])

    results = []
    for item in source or []:
        if not isinstance(item, dict):
            continue
        if item.get("media_type") not in {None, "movie"}:
            continue

        # For directors, keep actual directing jobs only.
        if role == "director" and item.get("job") != "Director":
            continue

        movie = dict(item)
        movie["tmdb_id"] = movie.get("id")
        movie["media_type"] = "movie"
        results.append(movie)

    return results


def get_movie_recommendations(movie_id, page=1):
    """Return TMDB's movie recommendation feed."""
    if not movie_id:
        return []

    data = _request(
        f"/movie/{int(movie_id)}/recommendations",
        params={
            "language": "en-US",
            "page": int(page),
        },
    )

    results = []
    for movie in data.get("results", []):
        if isinstance(movie, dict):
            movie = dict(movie)
            movie["tmdb_id"] = movie.get("id")
            movie["media_type"] = "movie"
            results.append(movie)

    return results


def get_movie_similar(movie_id, page=1):
    """Return TMDB's similar-movie feed."""
    if not movie_id:
        return []

    data = _request(
        f"/movie/{int(movie_id)}/similar",
        params={
            "language": "en-US",
            "page": int(page),
        },
    )

    results = []
    for movie in data.get("results", []):
        if isinstance(movie, dict):
            movie = dict(movie)
            movie["tmdb_id"] = movie.get("id")
            movie["media_type"] = "movie"
            results.append(movie)

    return results


def get_poster_url(poster_path, size="w500"):
    """
    Convert TMDB poster_path into a full image URL.
    """
    if not poster_path:
        return ""

    poster_path = str(poster_path).strip()

    if poster_path.startswith("http://") or poster_path.startswith("https://"):
        return poster_path

    if not poster_path.startswith("/"):
        poster_path = "/" + poster_path

    return f"https://image.tmdb.org/t/p/{size}{poster_path}"


def get_2026_movies(page=1):
    """
    Return released 2026 movies from TMDB.

    This was the original helper used by the homepage versions of WatchKaro.
    """
    today = datetime.now().date().isoformat()

    data = _request(
        "/discover/movie",
        params={
            "language": "en-US",
            "page": int(page),
            "include_adult": "false",
            "include_video": "false",
            "primary_release_date.gte": "2026-01-01",
            "primary_release_date.lte": today,
            "sort_by": "popularity.desc",
            "vote_count.gte": 5,
        },
    )

    results = []
    for movie in data.get("results", []):
        if isinstance(movie, dict):
            movie = dict(movie)
            movie["tmdb_id"] = movie.get("id")
            movie["media_type"] = "movie"
            results.append(movie)

    return results


def get_2026_movies_by_filter(
    genre_ids="",
    original_language="",
    sort_by="popularity.desc",
    page=1,
    vote_count_gte=5,
):
    """
    Generic 2026 discover helper kept for compatibility with earlier
    homepage experiments.
    """
    today = datetime.now().date().isoformat()

    languages = [
        code.strip().lower()
        for code in str(original_language or "").split("|")
        if code.strip()
    ]

    if not languages:
        languages = [""]

    all_results = []

    for language in languages:
        params = {
            "language": "en-US",
            "page": int(page),
            "include_adult": "false",
            "include_video": "false",
            "primary_release_date.gte": "2026-01-01",
            "primary_release_date.lte": today,
            "sort_by": sort_by,
            "vote_count.gte": int(vote_count_gte),
        }

        if genre_ids:
            params["with_genres"] = genre_ids

        if language:
            params["with_original_language"] = language

        data = _request("/discover/movie", params=params)

        for movie in data.get("results", []):
            if isinstance(movie, dict):
                movie = dict(movie)
                movie["tmdb_id"] = movie.get("id")
                movie["media_type"] = "movie"
                all_results.append(movie)

    seen = set()
    results = []

    for movie in all_results:
        movie_id = movie.get("tmdb_id", movie.get("id"))
        if movie_id in seen:
            continue
        seen.add(movie_id)
        results.append(movie)

    return results
