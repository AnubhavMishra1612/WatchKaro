# ============================================================
# MOVIE RECOMMENDER WEB APP
# ============================================================
#
# Final submission-ready Flask application.
#
# Features
# ------------------------------------------------------------
# - TMDB-only movie search
# - Exact / tolerant / prefix / contains / fuzzy title search
# - TMDB movie + TV search
# - Correct movie / TV selection handling
# - TMDB metadata-based recommendation engine
# - TMDB recommendations for TMDB-only movies
# - Live 2026 homepage sections
# - 2026 Trending & New
# - 2026 Recent Releases
# - 2026 Drama
# - 2026 Horror
# - 2026 Bollywood
# - 2026 Hollywood
# - Safe Pandas / NumPy -> JSON conversion
# - Request caching
# - TMDB caching
# - No local dataset dependency
# - Health endpoint
#
# IMPORTANT
# ------------------------------------------------------------
# final_recommender.py and tmdb_api.py are not required.
# All movie data and recommendations come directly from TMDB.
# ============================================================


# ============================================================
# IMPORTS
# ============================================================

import os
import re
import time
from bisect import bisect_left
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# APPLICATION
# ============================================================

app = Flask(__name__)


# ============================================================
# PROJECT CONFIGURATION
# ============================================================

SEARCH_LIMIT = 10
HOME_SECTION_LIMIT = 10

TMDB_MIN_CHARS = 3

SEARCH_CACHE_SIZE = 128
TMDB_SEARCH_CACHE_SIZE = 64
TMDB_RECOMMENDATION_CACHE_SIZE = 64
TMDB_DISCOVERY_CACHE_SIZE = 16

HOME_YEAR = 2026

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


# ============================================================
# TMDB API HELPERS — SELF CONTAINED
# ============================================================

# This app talks directly to TMDB. No tmdb_api.py, CSV, or local
# recommendation module is required.

def tmdb_get(path, params=None, timeout=15):
    if not TMDB_API_KEY and not TMDB_ACCESS_TOKEN:
        raise RuntimeError(
            "TMDB_API_KEY or TMDB_ACCESS_TOKEN is not configured."
        )

    request_params = dict(params or {})
    headers = dict(TMDB_HEADERS)

    # TMDB v3 accepts api_key as a query parameter.
    if TMDB_API_KEY and not TMDB_ACCESS_TOKEN:
        request_params["api_key"] = TMDB_API_KEY

    response = requests.get(
        f"{TMDB_BASE_URL}{path}",
        headers=headers,
        params=request_params,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def get_movie_recommendations(tmdb_id, page=1):
    payload = tmdb_get(
        f"/movie/{int(tmdb_id)}/recommendations",
        params={"language": "en-US", "page": page},
    )
    return payload.get("results", []) or []


def get_movie_details(tmdb_id):
    return tmdb_get(
        f"/movie/{int(tmdb_id)}",
        params={"language": "en-US", "append_to_response": "keywords,credits"},
    )


def search_movies(query, page=1):
    payload = tmdb_get(
        "/search/multi",
        params={
            "query": query,
            "language": "en-US",
            "include_adult": "false",
            "page": page,
        },
    )
    return payload.get("results", []) or []


def get_poster_url(path):
    if not path:
        return ""
    if str(path).startswith("http"):
        return str(path)
    return f"{TMDB_IMAGE_BASE}{path}"


# Compatibility name used by the search cache below.
tmdb_search_movies = search_movies


# ============================================================
# LANGUAGE NAMES
# ============================================================

LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "ml": "Malayalam",
    "kn": "Kannada",
    "bn": "Bengali",
    "mr": "Marathi",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ur": "Urdu",
    "fr": "French",
    "es": "Spanish",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ru": "Russian",
    "pt": "Portuguese",
    "ar": "Arabic",
    "tr": "Turkish",
    "th": "Thai",
    "id": "Indonesian",
    "vi": "Vietnamese",
    "fa": "Persian",
    "pl": "Polish",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "cs": "Czech",
    "el": "Greek",
}


# ============================================================
# SAFE CONVERSION HELPERS
# ============================================================

def safe_string(value, default=""):
    try:
        if value is None:
            return default

        if isinstance(value, (list, tuple, dict, set)):
            return default

        missing = pd.isna(value)

        if isinstance(missing, bool) and missing:
            return default

        return str(value)
    except Exception:
        return default


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        result = float(value)

        if pd.isna(result):
            return default

        return result
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default

        return int(float(value))
    except Exception:
        return default


def get_language_name(code):
    code = safe_string(code).strip().lower()

    if not code:
        return "Unknown"

    return LANGUAGE_NAMES.get(code, code.upper())


# ============================================================
# SEARCH NORMALIZATION
# ============================================================

def normalize_search_text(text):
    text = safe_string(text).lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def make_match_key(text):
    """
    Tolerant title key.
    Helps common repeated-vowel mistakes such as:
    bahubali / baahubali.
    """
    text = normalize_search_text(text)

    collapsed = re.sub(
        r"([aeiou])\1+",
        r"\1",
        text,
    )

    return collapsed


# ============================================================
# SEARCH ALIASES
# ============================================================

SEARCH_ALIASES = {
    "babubali": "baahubali",
    "bahubali": "baahubali",
    "bahubali the beginning": "baahubali the beginning",
    "bahubali 2": "baahubali 2",
    "spidar man": "spider man",
    "spiderman": "spider man",
    "interstelar": "interstellar",
    "intersteller": "interstellar",
}


def get_search_query(normalized_query):
    return SEARCH_ALIASES.get(
        normalized_query,
        normalized_query,
    )


# ============================================================
# TMDB-ONLY DATA SOURCE
# ============================================================
# No local CSV is loaded. WatchKaro is fully powered by TMDB.
# ============================================================

SEARCH_RECORDS = []
SORTED_SEARCH_TITLES = []
EXACT_INDEX = {}
MATCH_KEY_INDEX = {}

print()
print("=" * 70)
print("WATCHKARO — TMDB ONLY MODE")
print("Local clean_movies.csv: DISABLED")
print("=" * 70)


@lru_cache(maxsize=TMDB_DISCOVERY_CACHE_SIZE)
def discover_2026_movies(
    genre_ids="",
    original_language="",
    sort_by="popularity.desc",
    page=1,
    vote_count_gte=5,
):
    """
    Generic TMDB 2026 movie discovery.

    Arguments are cache-friendly immutable values so the
    function can safely be used with functools.lru_cache.
    """

    today = date.today().isoformat()

    params = {
        "language": "en-US",
        "page": page,
        "include_adult": "false",
        "include_video": "false",
        "primary_release_date.gte": f"{HOME_YEAR}-01-01",
        "primary_release_date.lte": today,
        "sort_by": sort_by,
        "vote_count.gte": vote_count_gte,
    }

    if genre_ids:
        params["with_genres"] = genre_ids

    # TMDB's original-language filter is safest when one ISO
    # language code is sent per request. The homepage South
    # Indian section uses "ta|te|ml|kn", so handle those codes
    # as separate discover calls and merge the results.
    language_codes = [
        code.strip().lower()
        for code in original_language.split("|")
        if code.strip()
    ]

    if not language_codes:
        language_codes = [""]

    all_results = []

    for language_code in language_codes:
        request_params = dict(params)

        if language_code:
            request_params["with_original_language"] = (
                language_code
            )

        data = tmdb_get(
            "/discover/movie",
            params=request_params,
            timeout=15,
        )

        for movie in data.get("results", []):
            if not isinstance(movie, dict):
                continue

            clean_movie = dict(movie)
            clean_movie["media_type"] = "movie"
            clean_movie["source"] = "TMDB"

            all_results.append(clean_movie)

    # Remove duplicates after multi-language discovery and keep
    # the requested sort order based on TMDB fields.
    deduplicated = {}
    for movie in all_results:
        movie_id = safe_string(
            movie.get("id", "")
        )

        if movie_id:
            deduplicated[movie_id] = movie
        else:
            deduplicated[
                (
                    safe_string(
                        movie.get("title", "")
                    ).lower(),
                    safe_string(
                        movie.get("release_date", "")
                    ),
                )
            ] = movie

    results = list(
        deduplicated.values()
    )

    reverse = not sort_by.endswith(".asc")

    if sort_by.startswith("primary_release_date"):
        results.sort(
            key=lambda movie: safe_string(
                movie.get(
                    "release_date",
                    "",
                )
            ),
            reverse=reverse,
        )
    else:
        results.sort(
            key=lambda movie: safe_float(
                movie.get(
                    "popularity",
                    0,
                )
            ),
            reverse=reverse,
        )

    return tuple(results)


@lru_cache(maxsize=TMDB_SEARCH_CACHE_SIZE)
def cached_tmdb_search(normalized_query):
    if not normalized_query:
        return tuple()

    corrected_query = get_search_query(
        normalized_query
    )

    try:
        results = tmdb_search_movies(
            corrected_query,
            page=1,
        )

        cleaned = []

        for movie in results or []:
            if not isinstance(movie, dict):
                continue

            media_type = movie.get(
                "media_type",
                "movie",
            )

            if media_type not in {
                "movie",
                "tv",
            }:
                continue

            clean_movie = dict(movie)
            clean_movie["source"] = "TMDB"
            cleaned.append(clean_movie)

        return tuple(cleaned[:20])

    except Exception as exc:
        print(
            "TMDB search error:",
            repr(exc),
        )

        return tuple()


@lru_cache(maxsize=TMDB_RECOMMENDATION_CACHE_SIZE)
def cached_tmdb_recommendations(tmdb_id):
    if not tmdb_id:
        return tuple()

    try:
        results = get_movie_recommendations(
            int(tmdb_id),
            page=1,
        )

        cleaned = []

        for movie in results or []:
            if not isinstance(movie, dict):
                continue

            clean_movie = dict(movie)
            clean_movie["source"] = "TMDB"
            clean_movie["media_type"] = "movie"
            cleaned.append(clean_movie)

        return tuple(cleaned[:20])

    except Exception as exc:
        print(
            "TMDB recommendation error:",
            repr(exc),
        )

        return tuple()


@lru_cache(maxsize=TMDB_RECOMMENDATION_CACHE_SIZE)
def cached_tmdb_similar_movies(tmdb_id):
    """
    Fetch TMDB's similar-movie list as a second candidate source.
    This is intentionally separate from recommendations because TMDB's
    recommendation feed can sometimes over-index on broad audiences.
    """
    if not tmdb_id:
        return tuple()

    try:
        payload = tmdb_get(
            f"/movie/{int(tmdb_id)}/similar",
            params={"page": 1},
        )

        results = payload.get("results", [])
        cleaned = []

        for movie in results or []:
            if not isinstance(movie, dict):
                continue

            clean_movie = dict(movie)
            clean_movie["source"] = "TMDB"
            clean_movie["media_type"] = "movie"
            cleaned.append(clean_movie)

        return tuple(cleaned[:20])

    except Exception as exc:
        print(
            "TMDB similar-movie error:",
            repr(exc),
        )
        return tuple()


# ============================================================
# SMART TMDB RECOMMENDATIONS — V2
# ============================================================

STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on",
    "for", "from", "with", "movie", "film", "series",
}

THEME_TERMS = {
    "mythology": {
        "mythology", "myth", "hindu", "hinduism", "ramayana",
        "mahabharata", "hanuman", "ram", "krishna", "deity",
        "god", "gods", "divine", "devotional", "epic", "legend",
    },
    "superhero": {
        "superhero", "superhero", "marvel", "dc", "batman",
        "superman", "spider", "avenger", "vigilante", "comic",
    },
    "horror": {
        "horror", "ghost", "haunted", "demon", "curse", "possession",
        "occult", "monster", "paranormal", "evil",
    },
    "romance": {
        "romance", "romantic", "love", "lover", "wedding", "marriage",
        "relationship",
    },
    "sports": {
        "sport", "sports", "cricket", "football", "boxing", "wrestling",
        "athlete", "coach",
    },
    "crime": {
        "crime", "criminal", "gangster", "mafia", "detective", "police",
        "murder", "heist", "thief",
    },
    "family": {
        "family", "kid", "kids", "child", "children", "school",
        "friendship", "animation", "cartoon",
    },
}


def _tmdb_text_words(text):
    return set(re.findall(r"[a-z0-9]+", safe_string(text).lower()))


@lru_cache(maxsize=TMDB_RECOMMENDATION_CACHE_SIZE * 2)
def _tmdb_details_cached(tmdb_id):
    """Get full TMDB movie details, including keywords and credits."""
    if not tmdb_id:
        return {}

    try:
        # One request gives us genres, keywords, cast and crew.
        details = tmdb_get(
            f"/movie/{int(tmdb_id)}",
            params={
                "language": "en-US",
                "append_to_response": "keywords,credits",
            },
            timeout=15,
        )
        return details if isinstance(details, dict) else {}
    except Exception as exc:
        print("TMDB details error:", repr(exc))
        try:
            details = get_movie_details(int(tmdb_id))
            return details if isinstance(details, dict) else {}
        except Exception:
            return {}


def _extract_tmdb_profile(details):
    genres = details.get("genres") or []
    genre_ids = {
        int(g.get("id"))
        for g in genres
        if isinstance(g, dict) and str(g.get("id", "")).isdigit()
    }
    genre_names = {
        safe_string(g.get("name", "")).lower()
        for g in genres
        if isinstance(g, dict) and safe_string(g.get("name", ""))
    }

    keywords_payload = details.get("keywords") or {}
    keywords = keywords_payload.get("keywords", []) if isinstance(keywords_payload, dict) else []
    keyword_names = {
        safe_string(k.get("name", "")).lower()
        for k in keywords
        if isinstance(k, dict) and safe_string(k.get("name", ""))
    }
    keyword_ids = [
        int(k.get("id"))
        for k in keywords
        if isinstance(k, dict) and str(k.get("id", "")).isdigit()
    ]

    title = safe_string(details.get("title", ""))
    overview = safe_string(details.get("overview", ""))
    tagline = safe_string(details.get("tagline", ""))
    language = safe_string(details.get("original_language", "")).lower()

    profile_text = " ".join([
        title,
        overview,
        tagline,
        " ".join(keyword_names),
        " ".join(genre_names),
    ])
    profile_words = _tmdb_text_words(profile_text)

    # Explicit title handling is deliberate: titles such as Hanuman Ans(h)
    # can have weak/absent keyword metadata in TMDB search results.
    title_words = _tmdb_text_words(title)
    if title_words.intersection({"hanuman", "hanu", "ram", "krishna", "adipurush"}):
        profile_words.update(THEME_TERMS["mythology"])

    active_themes = []
    for theme, terms in THEME_TERMS.items():
        if profile_words.intersection(terms):
            active_themes.append(theme)

    credits = details.get("credits") or {}
    cast = credits.get("cast") or []
    crew = credits.get("crew") or []
    actor_ids = {
        safe_int(person.get("id"), 0)
        for person in cast[:12]
        if isinstance(person, dict) and safe_int(person.get("id"), 0)
    }
    actor_names = {
        safe_string(person.get("name", "")).lower()
        for person in cast[:12]
        if isinstance(person, dict) and safe_string(person.get("name", ""))
    }
    director_ids = {
        safe_int(person.get("id"), 0)
        for person in crew
        if isinstance(person, dict)
        and safe_string(person.get("job", "")).lower() == "director"
        and safe_int(person.get("id"), 0)
    }
    director_names = {
        safe_string(person.get("name", "")).lower()
        for person in crew
        if isinstance(person, dict)
        and safe_string(person.get("job", "")).lower() == "director"
        and safe_string(person.get("name", ""))
    }

    return {
        "title": title,
        "overview": overview,
        "tagline": tagline,
        "language": language,
        "genre_ids": genre_ids,
        "genre_names": genre_names,
        "keyword_names": keyword_names,
        "keyword_ids": keyword_ids,
        "profile_words": profile_words,
        "active_themes": active_themes,
        "actor_ids": actor_ids,
        "actor_names": actor_names,
        "director_ids": director_ids,
        "director_names": director_names,
    }


@lru_cache(maxsize=TMDB_SEARCH_CACHE_SIZE)
def tmdb_movie_search(query):
    """Direct TMDB movie search; avoids the app's mixed movie/TV search path."""
    query = normalize_search_text(query)
    if not query:
        return tuple()

    try:
        payload = tmdb_get(
            "/search/movie",
            params={
                "query": query,
                "language": "en-US",
                "include_adult": "false",
                "page": 1,
            },
        )
        results = payload.get("results", [])
        return tuple(
            r for r in results
            if isinstance(r, dict)
        )
    except Exception as exc:
        print(f"TMDB direct movie search failed [{query}]:", repr(exc))
        return tuple()


@lru_cache(maxsize=TMDB_RECOMMENDATION_CACHE_SIZE)
def smart_tmdb_recommendations(tmdb_id, limit=20):
    """
    Lightweight TMDB-only recommender.

    It deliberately avoids searching dozens of extra titles and fetching full
    metadata for every candidate. That was causing excessive CPU/memory usage
    on Render's free instance. TMDB recommendations + similar movies provide
    the candidate pool; only the strongest candidates are enriched with
    credits for actor/director matching.
    """
    details = _tmdb_details_cached(str(tmdb_id))
    if not details:
        return tuple()

    profile = _extract_tmdb_profile(details)
    selected_language = profile.get("language", "").lower()
    selected_id = safe_string(details.get("id", tmdb_id))
    selected_title = normalize_search_text(profile.get("title", ""))
    selected_genres = profile.get("genre_ids", set())
    selected_words = profile.get("profile_words", set())
    selected_themes = set(profile.get("active_themes", []))

    # Two TMDB endpoints only for the initial candidate pool.
    recs = list(cached_tmdb_recommendations(str(tmdb_id)))
    similar = list(cached_tmdb_similar_movies(str(tmdb_id)))

    candidates = {}
    for rank, movie in enumerate(recs[:20]):
        if isinstance(movie, dict):
            mid = safe_string(movie.get("id", ""))
            if mid and mid != selected_id:
                candidates[mid] = {"movie": dict(movie), "source_score": 25.0 - rank * 0.5}

    for rank, movie in enumerate(similar[:20]):
        if isinstance(movie, dict):
            mid = safe_string(movie.get("id", ""))
            if mid and mid != selected_id:
                entry = candidates.setdefault(mid, {"movie": dict(movie), "source_score": 0.0})
                entry["source_score"] += max(5.0, 15.0 - rank * 0.35)

    ranked = []

    # Only enrich the first 12 candidates. This keeps one recommendation
    # request small enough for Render's free memory/CPU limits.
    for item in sorted(candidates.values(), key=lambda x: x["source_score"], reverse=True)[:8]:
        movie = item["movie"]
        mid = safe_string(movie.get("id", ""))
        candidate_details = _tmdb_details_cached(mid)
        if candidate_details:
            merged = dict(movie)
            merged.update(candidate_details)
            movie = merged

        language = safe_string(movie.get("original_language", "")).lower()

        # HARD language rule. If TMDB knows both languages, they must match.
        if selected_language and language and language != selected_language:
            continue

        candidate_profile = _extract_tmdb_profile(movie) if movie else {}
        candidate_genres = candidate_profile.get("genre_ids", set())
        candidate_words = _tmdb_text_words(" ".join([
            safe_string(movie.get("title", "")),
            safe_string(movie.get("overview", "")),
            " ".join(candidate_profile.get("keyword_names", set())),
        ]))

        actor_overlap = len(profile.get("actor_ids", set()) & candidate_profile.get("actor_ids", set()))
        director_overlap = len(profile.get("director_ids", set()) & candidate_profile.get("director_ids", set()))
        genre_overlap = len(selected_genres & candidate_genres)
        text_overlap = len(selected_words & candidate_words)

        theme_score = 0.0
        for theme in selected_themes:
            terms = THEME_TERMS.get(theme, set())
            hits = len(terms & candidate_words)
            if hits:
                theme_score += min(20.0, 8.0 + hits * 3.0)

        rating = safe_float(movie.get("vote_average"), 0.0)
        popularity = min(safe_float(movie.get("popularity"), 0.0), 100.0)

        score = (
            item["source_score"]
            + (18.0 if selected_language and language == selected_language else 0.0)
            + min(genre_overlap, 3) * 10.0
            + min(actor_overlap, 3) * 12.0
            + min(director_overlap, 1) * 20.0
            + min(text_overlap, 8) * 1.5
            + theme_score
            + (rating / 10.0) * 4.0
            + (popularity / 100.0) * 2.0
        )

        title = safe_string(movie.get("title", ""))
        if not title or normalize_search_text(title) == selected_title:
            continue

        movie["source"] = "TMDB"
        movie["media_type"] = "movie"
        movie["match_score"] = round(min(99.0, max(1.0, score)), 1)
        ranked.append((score, movie))

    ranked.sort(key=lambda x: x[0], reverse=True)
    result = [movie for _, movie in ranked[:limit]]

    # If the strict filter leaves too few results, do NOT mix languages.
    print("TMDB recommender:", profile.get("title"), "->", len(result), "results")
    return tuple(result)


# ============================================================
# MOVIE NORMALIZATION FOR THE EXISTING HTML
# ============================================================

def movie_to_dict(movie):
    """Convert any TMDB movie/TV dictionary into the fields index.html uses."""
    if not isinstance(movie, dict):
        return {
            "title": "Unknown", "name": "Unknown", "tmdb_id": "",
            "media_type": "movie", "source": "TMDB", "poster": "",
            "year": "", "language": "Unknown", "rating": 0,
            "genres": "", "overview": "", "cast": "", "director": "",
        }

    media_type = safe_string(movie.get("media_type", "movie")).lower()
    title = safe_string(movie.get("title") or movie.get("name") or "Unknown")
    release_date = safe_string(movie.get("release_date") or movie.get("first_air_date") or "")
    year = release_date[:4] if release_date[:4].isdigit() else ""
    language_code = safe_string(movie.get("original_language") or "").lower()

    genres = movie.get("genres") or []
    if isinstance(genres, list):
        genre_names = [
            safe_string(g.get("name"))
            for g in genres
            if isinstance(g, dict) and safe_string(g.get("name"))
        ]
    else:
        genre_names = []

    cast_names = []
    director_names = []
    credits = movie.get("credits") or {}
    if isinstance(credits, dict):
        cast = credits.get("cast") or []
        crew = credits.get("crew") or []
        cast_names = [
            safe_string(p.get("name"))
            for p in cast[:6]
            if isinstance(p, dict) and safe_string(p.get("name"))
        ]
        director_names = [
            safe_string(p.get("name"))
            for p in crew
            if isinstance(p, dict)
            and safe_string(p.get("job")).lower() == "director"
            and safe_string(p.get("name"))
        ][:2]

    return {
        "title": title,
        "name": title,
        "tmdb_id": safe_string(movie.get("id") or movie.get("tmdb_id") or ""),
        "id": movie.get("id"),
        "media_type": media_type or "movie",
        "source": safe_string(movie.get("source") or "TMDB"),
        "poster": get_poster_url(movie.get("poster_path") or movie.get("poster")),
        "year": year,
        "language": get_language_name(language_code),
        "language_code": language_code,
        "rating": round(safe_float(movie.get("vote_average"), 0.0), 1),
        "genres": ", ".join(genre_names),
        "overview": safe_string(movie.get("overview")),
        "cast": ", ".join(cast_names),
        "director": ", ".join(director_names),
        "popularity": safe_float(movie.get("popularity"), 0.0),
        "match_score": movie.get("match_score"),
    }


# ============================================================
# RECOMMENDATION NORMALIZATION
# ============================================================

def prepare_recommendations(recommendations):
    """Normalize TMDB recommendation dictionaries for the existing HTML."""
    if not recommendations:
        return []
    return [movie_to_dict(movie) for movie in recommendations if isinstance(movie, dict)]


# ============================================================
# TMDB HOMEPAGE SECTIONS
# ============================================================

HOME_SECTIONS = {
    "trending": [],
    "recent": [],
    "drama": [],
    "horror": [],
    "bollywood": [],
    "hollywood": [],
}


def discover_home_section(genre_ids="", original_language="", sort_by="popularity.desc", vote_count_gte=5):
    today = date.today().isoformat()
    params = {
        "language": "en-US",
        "page": 1,
        "include_adult": "false",
        "include_video": "false",
        "primary_release_date.gte": f"{HOME_YEAR}-01-01",
        "primary_release_date.lte": today,
        "sort_by": sort_by,
        "vote_count.gte": vote_count_gte,
    }
    if genre_ids:
        params["with_genres"] = genre_ids
    if original_language:
        params["with_original_language"] = original_language

    try:
        payload = tmdb_get("/discover/movie", params=params, timeout=15)
        results = []
        for movie in payload.get("results", [])[:HOME_SECTION_LIMIT]:
            if isinstance(movie, dict):
                movie = dict(movie)
                movie["media_type"] = "movie"
                movie["source"] = "TMDB"
                results.append(movie_to_dict(movie))
        return results
    except Exception as exc:
        print("Homepage discovery error:", repr(exc))
        return []


def build_home_sections():
    jobs = {
        "trending": dict(sort_by="popularity.desc", vote_count_gte=5),
        "recent": dict(sort_by="primary_release_date.desc", vote_count_gte=3),
        "drama": dict(genre_ids="18", sort_by="popularity.desc", vote_count_gte=5),
        "horror": dict(genre_ids="27", sort_by="popularity.desc", vote_count_gte=5),
        "bollywood": dict(original_language="hi", sort_by="popularity.desc", vote_count_gte=5),
        "hollywood": dict(original_language="en", sort_by="popularity.desc", vote_count_gte=10),
    }
    home = {key: [] for key in jobs}
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {executor.submit(discover_home_section, **config): key for key, config in jobs.items()}
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                home[key] = future.result()
            except Exception as exc:
                print(f"Homepage section {key} failed:", repr(exc))
    return home


def get_home_sections():
    global HOME_SECTIONS
    if not any(HOME_SECTIONS.values()):
        HOME_SECTIONS = build_home_sections()
    return HOME_SECTIONS


# ============================================================
# SEARCH ROUTE
# ============================================================

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])

    results = list(cached_tmdb_search(normalize_search_text(query)))
    return jsonify([movie_to_dict(movie) for movie in results[:SEARCH_LIMIT]])


@app.route("/")
def home():
    return render_template(
        "index.html",
        selected_movie=None,
        recommendations=[],
        search_results=[],
        selected_media_type=None,
        error=None,
        notice=None,
        home_sections=get_home_sections(),
    )


# ============================================================
# RECOMMENDATION ROUTE
# ============================================================

@app.route(
    "/recommend",
    methods=["GET", "POST"],
)
def recommendation():
    request_start = time.perf_counter()

    title = request.args.get("movie", "").strip()
    selected_media_type = request.args.get("media_type", "").strip().lower()
    selected_source = request.args.get("source", "").strip().upper()
    selected_tmdb_id = request.args.get("tmdb_id", "").strip()

    if request.method == "POST":
        title = (request.form.get("movie") or request.form.get("title") or title).strip()
        selected_media_type = request.form.get("media_type", selected_media_type).strip().lower()
        selected_source = request.form.get("source", selected_source).strip().upper()
        selected_tmdb_id = request.form.get("tmdb_id", selected_tmdb_id).strip()

    if not title:
        return render_template(
            "index.html", selected_movie=None, recommendations=[], search_results=[],
            selected_media_type=None, error=None,
            notice=None, home_sections=get_home_sections(),
        )

    tmdb_results = list(cached_tmdb_search(normalize_search_text(title)))
    selected_tmdb = None

    # Respect an explicit TMDB card selection.
    if selected_source == "TMDB" and selected_tmdb_id:
        for movie in tmdb_results:
            if safe_string(movie.get("id", movie.get("tmdb_id", ""))) == selected_tmdb_id:
                selected_tmdb = movie
                break

    # Otherwise prefer exact title, then movie over TV, then first result.
    normalized_title = normalize_search_text(title)
    if selected_tmdb is None:
        exact = [
            m for m in tmdb_results
            if normalize_search_text(m.get("title", m.get("name", ""))) == normalized_title
        ]
        if exact:
            selected_tmdb = next((m for m in exact if m.get("media_type") == "movie"), exact[0])

    if selected_tmdb is None:
        selected_tmdb = next((m for m in tmdb_results if m.get("media_type") == "movie"), None)
    if selected_tmdb is None and tmdb_results:
        selected_tmdb = tmdb_results[0]

    if selected_tmdb is None:
        return render_template(
            "index.html", selected_movie=None, recommendations=[], search_results=[],
            selected_media_type=None,
            error=f"'{title}' was not found on TMDB.", notice=None,
            home_sections=get_home_sections(),
        )

    # Fetch full metadata once the user has selected a title. This supplies
    # cast/director/genres/overview while keeping the app completely TMDB-only.
    selected_full = selected_tmdb
    if safe_string(selected_tmdb.get("media_type", "movie")) == "movie":
        selected_id = safe_string(selected_tmdb.get("id", ""))
        if selected_id:
            full_details = _tmdb_details_cached(selected_id)
            if full_details:
                selected_full = dict(selected_tmdb)
                selected_full.update(full_details)
                selected_full["media_type"] = "movie"

    selected_movie = movie_to_dict(selected_full)
    selected_movie["source"] = "TMDB"
    media_type = selected_movie.get("media_type", "movie")

    if media_type == "tv":
        return render_template(
            "index.html", selected_movie=selected_movie, recommendations=[], search_results=[],
            selected_media_type="tv", error=None,
            notice="This is a TV series. TV recommendations are not enabled yet.",
            home_sections=get_home_sections(),
        )

    tmdb_id = safe_string(selected_movie.get("tmdb_id", ""))
    recommendations = [
        movie_to_dict(movie)
        for movie in smart_tmdb_recommendations(tmdb_id, 20)
    ]

    total_time = time.perf_counter() - request_start
    print("TMDB movie:", selected_movie["title"])
    print("Recommendations:", len(recommendations))
    print(f"TOTAL REQUEST TIME: {total_time:.4f}s")

    return render_template(
        "index.html", selected_movie=selected_movie, recommendations=recommendations,
        search_results=[], selected_media_type="movie", error=None, notice=None,
        home_sections=get_home_sections(),
    )


# ============================================================
# HEALTH ENDPOINT
# ============================================================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "data_source": "TMDB",
        "local_dataset_required": False,
        "home_sections": {
            name: len(get_home_sections()[name])
            for name in ("trending", "recent", "drama", "horror", "bollywood", "hollywood")
        },
        "cache": {
            "tmdb_search": cached_tmdb_search.cache_info()._asdict(),
            "tmdb_recommendations": cached_tmdb_recommendations.cache_info()._asdict(),
            "tmdb_discovery": discover_2026_movies.cache_info()._asdict(),
        },
    })


# ============================================================
# START FLASK
# ============================================================

if __name__ == "__main__":
    print()
    print("=" * 70)
    print("MOVIE RECOMMENDER WEB APP")
    print("=" * 70)
    print("Data source   : TMDB only")
    print("Local CSV     : not required")
    print()
    print("Open:")
    print(
        "http://127.0.0.1:5000/"
    )
    print()
    print("Health:")
    print(
        "http://127.0.0.1:5000/health"
    )
    print()
    print(
        "Press CTRL+C to stop."
    )
    print("=" * 70)

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        use_reloader=False,
        threaded=True,
    )
