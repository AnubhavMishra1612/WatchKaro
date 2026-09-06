# ============================================================
# MOVIE RECOMMENDER WEB APP
# ============================================================
#
# Final submission-ready Flask application.
#
# Features
# ------------------------------------------------------------
# - 78k+ local movie dataset search
# - Exact / tolerant / prefix / contains / fuzzy title search
# - TMDB movie + TV search
# - Correct movie / TV selection handling
# - V4 local recommendation engine
# - TMDB recommendations for TMDB-only movies
# - Live 2026 homepage sections
# - 2026 Trending & New
# - 2026 Recent Releases
# - 2026 Mystery & Thriller
# - 2026 Bollywood
# - 2026 South Indian Cinema
# - 2026 English Movies
# - 2026 Animation & Cartoons
# - Safe Pandas / NumPy -> JSON conversion
# - Request caching
# - TMDB caching
# - Graceful TMDB fallback to local 2026 data
# - Health endpoint
#
# IMPORTANT
# ------------------------------------------------------------
# final_recommender.py is NOT modified.
# tmdb_api.py is still used for search/recommendations.
# TMDB homepage discovery is handled safely in this file so
# this app does not depend on a new helper being added to
# tmdb_api.py.
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

import pandas as pd
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

DATA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "clean_movies.csv",
)

SEARCH_LIMIT = 10
HOME_SECTION_LIMIT = 10

TMDB_MIN_CHARS = 3

SEARCH_CACHE_SIZE = 512
V4_CACHE_SIZE = 256
TMDB_SEARCH_CACHE_SIZE = 256
TMDB_RECOMMENDATION_CACHE_SIZE = 256
TMDB_DISCOVERY_CACHE_SIZE = 64

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
# EXISTING PROJECT MODULES
# ============================================================

# ------------------------------------------------------------
# Render compatibility for older final_recommender.py
# ------------------------------------------------------------
# Some older copies of final_recommender.py still contain the
# Windows-only path C:\movie-recommender\data\clean_movies.csv.
# During its import, redirect only that legacy path to this
# deployment-safe dataset path, then restore pandas.read_csv.
_original_read_csv = pd.read_csv

def _render_read_csv(filepath_or_buffer, *args, **kwargs):
    if isinstance(filepath_or_buffer, (str, os.PathLike)):
        raw_path = os.path.normpath(os.fspath(filepath_or_buffer))
        legacy_path = os.path.normpath(
            r"C:\movie-recommender\data\clean_movies.csv"
        )
        if raw_path.lower() == legacy_path.lower():
            filepath_or_buffer = DATA_PATH
    return _original_read_csv(filepath_or_buffer, *args, **kwargs)

pd.read_csv = _render_read_csv
try:
    from final_recommender import recommend as v4_recommend
finally:
    pd.read_csv = _original_read_csv


from tmdb_api import (
    search_movies as tmdb_search_movies,
    get_movie_recommendations,
    get_movie_details,
    get_poster_url,
)


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
# LOAD LOCAL DATASET
# ============================================================

print()
print("=" * 70)
print("LOADING LOCAL MOVIE DATASET")
print("=" * 70)

load_start = time.perf_counter()

if not os.path.exists(DATA_PATH):
    raise FileNotFoundError(
        f"Dataset not found:\n{DATA_PATH}"
    )

df = pd.read_csv(DATA_PATH)

load_time = time.perf_counter() - load_start

print(f"Movies loaded : {len(df):,}")
print(f"Load time     : {load_time:.2f} seconds")

if "title" not in df.columns:
    raise ValueError(
        "Required dataset column missing: title"
    )


# ============================================================
# BUILD FAST SEARCH INDEX
# ============================================================

print()
print("Building fast search index...")

search_index_start = time.perf_counter()

_search_work = df["title"].fillna("").astype(str)

SEARCH_RECORDS = sorted(
    (
        normalize_search_text(title),
        index,
    )
    for index, title in _search_work.items()
    if normalize_search_text(title)
)

SORTED_SEARCH_TITLES = [
    item[0]
    for item in SEARCH_RECORDS
]

EXACT_INDEX = {}
MATCH_KEY_INDEX = {}

for position, (title_text, idx) in enumerate(
    SEARCH_RECORDS
):
    EXACT_INDEX.setdefault(
        title_text,
        [],
    ).append(idx)

    MATCH_KEY_INDEX.setdefault(
        make_match_key(title_text),
        [],
    ).append(idx)

print(
    f"Search index ready : {len(SEARCH_RECORDS):,} titles"
)

print(
    "Index build time   : "
    f"{time.perf_counter() - search_index_start:.2f} seconds"
)


# ============================================================
# LOCAL SEARCH
# ============================================================

@lru_cache(maxsize=SEARCH_CACHE_SIZE)
def _local_search_cached(
    normalized_query,
    limit,
):
    if not normalized_query:
        return tuple()

    matches = {}

    # --------------------------------------------------------
    # Exact
    # --------------------------------------------------------

    for idx in EXACT_INDEX.get(
        normalized_query,
        [],
    ):
        matches[idx] = 2_000_000

    # --------------------------------------------------------
    # Tolerant exact
    # --------------------------------------------------------

    query_key = make_match_key(
        normalized_query
    )

    for idx in MATCH_KEY_INDEX.get(
        query_key,
        [],
    ):
        matches.setdefault(
            idx,
            1_900_000,
        )

    # --------------------------------------------------------
    # Prefix
    # --------------------------------------------------------

    start_position = bisect_left(
        SORTED_SEARCH_TITLES,
        normalized_query,
    )

    for position in range(
        start_position,
        len(SEARCH_RECORDS),
    ):
        title_text, idx = (
            SEARCH_RECORDS[position]
        )

        if not title_text.startswith(
            normalized_query
        ):
            break

        matches.setdefault(
            idx,
            1_000_000,
        )

    # --------------------------------------------------------
    # Word start
    # --------------------------------------------------------

    query_words = normalized_query.split()

    for title_text, idx in SEARCH_RECORDS:
        if idx in matches:
            continue

        title_words = title_text.split()

        if any(
            any(
                word.startswith(query_word)
                for word in title_words
            )
            for query_word in query_words
        ):
            matches[idx] = 800_000

    # --------------------------------------------------------
    # Contains
    # --------------------------------------------------------

    for title_text, idx in SEARCH_RECORDS:
        if idx in matches:
            continue

        if normalized_query in title_text:
            matches[idx] = 600_000

    # --------------------------------------------------------
    # Fuzzy fallback
    # --------------------------------------------------------

    if not matches:
        for title_text, idx in SEARCH_RECORDS:
            similarity = SequenceMatcher(
                None,
                get_search_query(normalized_query),
                title_text,
            ).ratio()

            if similarity >= 0.78:
                matches[idx] = (
                    300_000
                    + int(similarity * 100_000)
                )

    # --------------------------------------------------------
    # Rank
    # --------------------------------------------------------

    ranked = sorted(
        matches.items(),
        key=lambda pair: (
            pair[1],
            safe_float(
                df.iloc[pair[0]].get(
                    "vote_count",
                    0,
                )
            ),
            safe_float(
                df.iloc[pair[0]].get(
                    "popularity",
                    0,
                )
            ),
        ),
        reverse=True,
    )

    return tuple(
        pair[0]
        for pair in ranked[:limit]
    )


def movie_to_dict(movie):
    """
    Convert DataFrame Series / dict / TMDB movie into
    JSON-safe frontend data.
    """

    def value_from(keys, default=""):
        if isinstance(movie, pd.Series):
            for key in keys:
                if key in movie.index:
                    value = movie[key]
                    if not pd.isna(value):
                        return value
            return default

        if isinstance(movie, dict):
            for key in keys:
                if key in movie:
                    value = movie[key]
                    if value is not None:
                        return value
            return default

        return default

    title = safe_string(
        value_from(
            ["title", "name"],
            "",
        )
    ).strip()

    release_date = safe_string(
        value_from(
            ["release_date", "first_air_date"],
            "",
        )
    ).strip()

    year = release_date[:4] if release_date else ""

    language_code = safe_string(
        value_from(
            [
                "original_language",
                "language_code",
                "language",
            ],
            "",
        )
    ).lower().strip()

    language = (
        get_language_name(language_code)
        if language_code
        else safe_string(
            value_from(["language"], "Unknown")
        )
    )

    genres = safe_string(
        value_from(
            ["genres"],
            "",
        )
    ).strip()

    rating = safe_float(
        value_from(
            ["vote_average", "rating"],
            0,
        )
    )

    votes = safe_int(
        value_from(
            ["vote_count", "votes"],
            0,
        )
    )

    popularity = safe_float(
        value_from(
            ["popularity"],
            0,
        )
    )

    overview = safe_string(
        value_from(
            ["overview"],
            "",
        )
    ).strip()

    poster_path = safe_string(
        value_from(
            ["poster_path"],
            "",
        )
    ).strip()

    poster = safe_string(
        value_from(
            ["poster"],
            "",
        )
    ).strip()

    tmdb_id = value_from(
        ["tmdb_id", "id"],
        "",
    )

    if isinstance(tmdb_id, float) and pd.isna(tmdb_id):
        tmdb_id = ""

    try:
        if safe_string(tmdb_id).strip().isdigit():
            tmdb_id = int(
                safe_string(tmdb_id).strip()
            )
    except Exception:
        pass

    media_type = safe_string(
        value_from(
            ["media_type"],
            "movie",
        )
    ).lower()

    if media_type not in {"movie", "tv"}:
        media_type = "movie"

    source = safe_string(
        value_from(
            ["source"],
            "LOCAL",
        )
    ).upper()

    if not poster and poster_path:
        try:
            poster = (
                get_poster_url(poster_path)
                if poster_path.startswith("/")
                else poster_path
            )
        except Exception:
            if poster_path.startswith("http"):
                poster = poster_path

    return {
        "title": title,
        "year": year,
        "release_date": release_date,
        "language": language,
        "language_code": language_code,
        "genres": genres,
        "rating": round(rating, 1),
        "votes": votes,
        "popularity": round(popularity, 2),
        "overview": overview,
        "poster": poster,
        "poster_path": poster_path,
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "source": source,
    }


def search_movies(query, limit=SEARCH_LIMIT):
    normalized_query = normalize_search_text(query)

    if not normalized_query:
        return []

    corrected_query = get_search_query(
        normalized_query
    )

    indices = _local_search_cached(
        corrected_query,
        limit,
    )

    results = []

    for idx in indices:
        movie = movie_to_dict(
            df.iloc[idx]
        )

        movie["source"] = "LOCAL"
        movie["media_type"] = "movie"

        results.append(movie)

    return results


# ============================================================
# TMDB HELPERS
# ============================================================

def tmdb_get(path, params=None, timeout=15):
    if not TMDB_API_KEY and not TMDB_ACCESS_TOKEN:
        raise RuntimeError(
            "TMDB credentials are not configured in .env"
        )

    response = requests.get(
        f"{TMDB_BASE_URL}{path}",
        headers=TMDB_HEADERS,
        params=params or {},
        timeout=timeout,
    )

    response.raise_for_status()

    return response.json()


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


def _tmdb_details_cached(tmdb_id):
    """Get full selected-movie details, including TMDB keywords."""
    if not tmdb_id:
        return {}

    try:
        details = get_movie_details(int(tmdb_id))
        return details if isinstance(details, dict) else {}
    except Exception as exc:
        print("TMDB details error:", repr(exc))
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
    Build recommendations from fresh TMDB metadata instead of trusting
    TMDB's recommendation order.

    Important change: for strongly themed movies we can completely exclude
    weak generic recommendations that do not match the selected movie's
    theme. This fixes cases such as Hanuman Ansh -> unrelated family films.
    """
    details = _tmdb_details_cached(str(tmdb_id))
    if not details:
        return tuple()

    profile = _extract_tmdb_profile(details)

    selected_id = safe_string(details.get("id", tmdb_id))
    selected_title = normalize_search_text(profile["title"])

    # ------------------------------------------------------------
    # Candidate query generation
    # ------------------------------------------------------------
    queries = []

    # Title phrases first.
    title_words = [
        w for w in profile["title"].lower().split()
        if w not in STOPWORDS and len(w) >= 4
    ]
    queries.extend(title_words[:3])

    # Strong keyword phrases next.
    useful_keywords = []
    for keyword in sorted(profile["keyword_names"]):
        normalized = normalize_search_text(keyword)
        if normalized and len(normalized) >= 4 and normalized not in STOPWORDS:
            if not any(normalized == q for q in useful_keywords):
                useful_keywords.append(normalized)
    queries.extend(useful_keywords[:6])

    # Theme queries are the fallback/expansion layer.
    for theme in profile["active_themes"]:
        if theme == "mythology":
            queries.extend(["hanuman", "ramayana", "krishna", "hindu mythology", "adipurush"])
        elif theme == "superhero":
            queries.extend(["superhero", "marvel", "dc", "superman", "batman"])
        elif theme == "horror":
            queries.extend(["horror", "ghost", "demon", "haunted"])
        elif theme == "romance":
            queries.extend(["romance", "romantic", "love"])
        elif theme == "sports":
            queries.extend(["sports", "cricket", "football", "boxing"])
        elif theme == "crime":
            queries.extend(["crime", "gangster", "detective", "heist"])
        elif theme == "family":
            queries.extend(["family", "kids", "animation", "cartoon"])

    # One broad genre query is useful when keyword metadata is sparse.
    queries.extend(sorted(profile["genre_names"])[:2])

    # Preserve order + remove duplicates.
    seen_queries = set()
    final_queries = []
    for q in queries:
        nq = normalize_search_text(q)
        if nq and nq not in seen_queries:
            seen_queries.add(nq)
            final_queries.append(nq)

    # ------------------------------------------------------------
    # Gather candidates
    # ------------------------------------------------------------
    candidates = {}

    def add_candidate(movie, query, weight):
        if not isinstance(movie, dict):
            return
        movie_id = safe_string(movie.get("id", ""))
        if not movie_id or movie_id == selected_id:
            return

        entry = candidates.setdefault(
            movie_id,
            {"movie": dict(movie), "query_hits": {}, "query_weight": 0.0},
        )
        entry["query_hits"][query] = entry["query_hits"].get(query, 0) + 1
        entry["query_weight"] += weight

    # Search candidates from theme/title/keyword queries.
    for query in final_queries[:12]:
        results = tmdb_movie_search(query)
        for rank, movie in enumerate(results[:10]):
            add_candidate(movie, query, max(1.0, 10.0 - rank))

    # Add TMDB recommendation + similar feeds only as a secondary source.
    try:
        base_recommendations = get_movie_recommendations(
            int(tmdb_id),
            page=1,
        ) or []
    except Exception:
        base_recommendations = []

    try:
        similar_payload = tmdb_get(
            f"/movie/{int(tmdb_id)}/similar",
            params={"page": 1},
        )
        base_similar = similar_payload.get("results", []) or []
    except Exception:
        base_similar = []

    for rank, movie in enumerate(base_recommendations[:20]):
        add_candidate(movie, "__recommendations__", max(0.25, 2.0 - rank * 0.05))

    for rank, movie in enumerate(base_similar[:20]):
        add_candidate(movie, "__similar__", max(0.15, 1.5 - rank * 0.04))

    # ------------------------------------------------------------
    # Rank candidates
    # ------------------------------------------------------------
    ranked = []
    strong_theme = bool(profile["active_themes"])

    for item in candidates.values():
        movie = item["movie"]
        title = safe_string(movie.get("title", ""))
        title_norm = normalize_search_text(title)
        if not title_norm or title_norm == selected_title:
            continue

        candidate_words = _tmdb_text_words(" ".join([
            title,
            safe_string(movie.get("overview", "")),
        ]))

        candidate_genres = {
            int(x) for x in (movie.get("genre_ids") or [])
            if str(x).isdigit()
        }
        genre_overlap = len(profile["genre_ids"].intersection(candidate_genres))

        language = safe_string(movie.get("original_language", "")).lower()
        lang_score = 16.0 if profile["language"] and language == profile["language"] else 0.0
        if profile["language"] in {"hi", "ta", "te", "ml", "kn"} and language in {"hi", "ta", "te", "ml", "kn"}:
            lang_score = max(lang_score, 8.0)

        profile_overlap = len(profile["profile_words"].intersection(candidate_words))
        text_score = min(profile_overlap * 2.0, 16.0)
        genre_score = min(genre_overlap, 3) * 12.0
        query_score = min(item["query_weight"], 30.0)

        theme_score = 0.0
        theme_mismatch = 0.0
        for theme in profile["active_themes"]:
            terms = THEME_TERMS[theme]
            hit_count = len(terms.intersection(candidate_words))
            if hit_count:
                theme_score += min(12.0 + hit_count * 3.0, 24.0)
            elif strong_theme:
                theme_mismatch += 20.0

        rating = safe_float(movie.get("vote_average", 0.0))
        popularity = min(safe_float(movie.get("popularity", 0.0)), 100.0)
        quality_score = (rating / 10.0) * 6.0 + (popularity / 100.0) * 3.0

        # For strong themes, generic TMDB recommendations must prove relevance.
        if strong_theme and theme_score == 0 and query_score < 4.0:
            theme_mismatch += 12.0

        total = (
            query_score
            + genre_score
            + lang_score
            + text_score
            + theme_score
            + quality_score
            - theme_mismatch
        )

        ranked.append((total, movie))

    ranked.sort(key=lambda item: item[0], reverse=True)

    top = [movie for _, movie in ranked[:limit]]

    print("=" * 60)
    print("SMART TMDB RECOMMENDER")
    print("Selected:", profile["title"])
    print("Themes:", profile["active_themes"])
    print("Queries:", final_queries[:12])
    print("Top recommendations:")
    for i, movie in enumerate(top[:10], 1):
        print(f"  {i:02d}. {movie.get('title', 'Unknown')}")
    print("=" * 60)

    return tuple(top)

# ============================================================
# HOME DATA HELPERS
# ============================================================

def get_local_2026_movies(
    work,
    limit=HOME_SECTION_LIMIT,
    language_codes=None,
    genre_terms=None,
):
    frame = work.copy()

    mask = (
        frame["_home_release_date"].notna()
        & (
            frame["_home_release_date"].dt.year
            == HOME_YEAR
        )
        & (
            frame["_home_release_date"]
            <= pd.Timestamp(date.today())
        )
    )

    if language_codes is not None:
        if "original_language" in frame.columns:
            languages = (
                frame["original_language"]
                .fillna("")
                .astype(str)
                .str.lower()
            )

            mask &= languages.isin(
                language_codes
            )

    if genre_terms:
        if "genres" in frame.columns:
            genres = (
                frame["genres"]
                .fillna("")
                .astype(str)
                .str.lower()
            )

            genre_mask = pd.Series(
                False,
                index=frame.index,
            )

            for term in genre_terms:
                genre_mask |= genres.str.contains(
                    term,
                    na=False,
                )

            mask &= genre_mask

    filtered = frame[mask].copy()

    filtered = filtered.sort_values(
        [
            "_home_release_date",
            "_home_popularity",
            "_home_rating",
            "_home_votes",
        ],
        ascending=[
            False,
            False,
            False,
            False,
        ],
    )

    return dataframe_to_home_movies(
        filtered,
        limit,
    )


def dataframe_to_home_movies(
    frame,
    limit=HOME_SECTION_LIMIT,
):
    if frame is None or frame.empty:
        return []

    work = frame.copy()

    work["_home_title"] = (
        work["title"]
        .fillna("")
        .astype(str)
        .map(normalize_search_text)
    )

    work = work[
        work["_home_title"] != ""
    ]

    work = work.drop_duplicates(
        "_home_title"
    )

    work = work.head(limit)

    results = []

    for _, movie in work.iterrows():
        data = movie_to_dict(movie)
        data["source"] = "LOCAL"
        data["media_type"] = "movie"
        results.append(data)

    return results


def local_home_frame():
    work = df.copy()

    work["_home_popularity"] = pd.to_numeric(
        work["popularity"]
        if "popularity" in work.columns
        else 0,
        errors="coerce",
    ).fillna(0)

    work["_home_votes"] = pd.to_numeric(
        work["vote_count"]
        if "vote_count" in work.columns
        else 0,
        errors="coerce",
    ).fillna(0)

    work["_home_rating"] = pd.to_numeric(
        work["vote_average"]
        if "vote_average" in work.columns
        else 0,
        errors="coerce",
    ).fillna(0)

    if "release_date" in work.columns:
        work["_home_release_date"] = pd.to_datetime(
            work["release_date"],
            errors="coerce",
        )
    else:
        work["_home_release_date"] = pd.NaT

    if "adult" in work.columns:
        adult_values = (
            work["adult"]
            .astype(str)
            .str.lower()
        )

        work = work[
            adult_values != "true"
        ]

    return work


# ============================================================
# HOME SECTION DISCOVERY
# ============================================================

def discover_home_section(
    name,
    *,
    genre_ids="",
    original_language="",
    sort_by="popularity.desc",
    vote_count_gte=5,
):
    try:
        print(
            f"Loading 2026 {name} movies from TMDB..."
        )

        movies = discover_2026_movies(
            genre_ids=genre_ids,
            original_language=original_language,
            sort_by=sort_by,
            page=1,
            vote_count_gte=vote_count_gte,
        )

        converted = [
            movie_to_dict(movie)
            for movie in movies[:HOME_SECTION_LIMIT]
        ]

        print(
            f"2026 {name} loaded:",
            len(converted),
        )

        return converted

    except Exception as exc:
        print(
            f"TMDB {name} error:",
            repr(exc),
        )

        return []


def build_home_sections():
    """
    Build all homepage sections once when Flask starts.

    Every 2026-labelled category is filtered to 2026.
    TMDB is preferred; local 2026-only data is the fallback.
    """

    work = local_home_frame()

    home = {
        "trending": [],
        "recent": [],
        "mystery": [],
        "bollywood": [],
        "south": [],
        "english": [],
        "animation": [],
    }

    # --------------------------------------------------------
    # TMDB discovery jobs
    # --------------------------------------------------------

    jobs = {
        "trending": {
            "name": "Trending & New",
            "sort_by": "popularity.desc",
            "vote_count_gte": 5,
        },
        "recent": {
            "name": "Recent Releases",
            "sort_by": "primary_release_date.desc",
            "vote_count_gte": 3,
        },
        "mystery": {
            "name": "Mystery & Thriller",
            "genre_ids": "9648|53",
            "sort_by": "popularity.desc",
            "vote_count_gte": 10,
        },
        "bollywood": {
            "name": "Bollywood",
            "original_language": "hi",
            "sort_by": "popularity.desc",
            "vote_count_gte": 5,
        },
        "south": {
            "name": "South Indian",
            "original_language": "ta|te|ml|kn",
            "sort_by": "popularity.desc",
            "vote_count_gte": 5,
        },
        "english": {
            "name": "English",
            "original_language": "en",
            "sort_by": "popularity.desc",
            "vote_count_gte": 10,
        },
        "animation": {
            "name": "Animation & Cartoons",
            "genre_ids": "16",
            "sort_by": "popularity.desc",
            "vote_count_gte": 5,
        },
    }

    print()
    print("=" * 70)
    print("BUILDING 2026 HOMEPAGE SECTIONS")
    print("=" * 70)

    with ThreadPoolExecutor(
        max_workers=7
    ) as executor:

        future_map = {
            executor.submit(
                discover_home_section,
                config["name"],
                genre_ids=config.get(
                    "genre_ids",
                    "",
                ),
                original_language=config.get(
                    "original_language",
                    "",
                ),
                sort_by=config.get(
                    "sort_by",
                    "popularity.desc",
                ),
                vote_count_gte=config.get(
                    "vote_count_gte",
                    5,
                ),
            ): key
            for key, config in jobs.items()
        }

        for future in as_completed(
            future_map
        ):
            key = future_map[future]

            try:
                home[key] = future.result()
            except Exception as exc:
                print(
                    f"Homepage section {key} failed:",
                    repr(exc),
                )
                home[key] = []

    # --------------------------------------------------------
    # Local 2026 fallbacks
    # --------------------------------------------------------

    if not home["trending"]:
        home["trending"] = get_local_2026_movies(
            work,
            HOME_SECTION_LIMIT,
        )

    if not home["recent"]:
        home["recent"] = get_local_2026_movies(
            work,
            HOME_SECTION_LIMIT,
        )

    if not home["mystery"]:
        home["mystery"] = get_local_2026_movies(
            work,
            HOME_SECTION_LIMIT,
            genre_terms=["mystery", "thriller"],
        )

    if not home["bollywood"]:
        home["bollywood"] = get_local_2026_movies(
            work,
            HOME_SECTION_LIMIT,
            language_codes={"hi"},
        )

    if not home["south"]:
        home["south"] = get_local_2026_movies(
            work,
            HOME_SECTION_LIMIT,
            language_codes={
                "ta",
                "te",
                "ml",
                "kn",
            },
        )

    if not home["english"]:
        home["english"] = get_local_2026_movies(
            work,
            HOME_SECTION_LIMIT,
            language_codes={"en"},
        )

    if not home["animation"]:
        home["animation"] = get_local_2026_movies(
            work,
            HOME_SECTION_LIMIT,
            genre_terms=["animation"],
        )

    return home


# ============================================================
# BUILD HOMEPAGE DATA ONCE
# ============================================================

home_start = time.perf_counter()

HOME_SECTIONS = build_home_sections()

print()
print(
    f"Homepage ready in "
    f"{time.perf_counter() - home_start:.2f} seconds"
)

for section_name in (
    "trending",
    "recent",
    "mystery",
    "bollywood",
    "south",
    "english",
    "animation",
):
    print(
        f"{section_name.title():<15}: "
        f"{len(HOME_SECTIONS[section_name])}"
    )

print("=" * 70)


# ============================================================
# RECOMMENDATION NORMALIZATION
# ============================================================

def prepare_recommendations(
    recommendations,
):
    if recommendations is None:
        return []

    results = []

    if isinstance(
        recommendations,
        pd.DataFrame,
    ):
        for _, movie in recommendations.iterrows():
            movie_data = movie_to_dict(movie)

            if "score" in movie.index:
                movie_data["score"] = round(
                    safe_float(
                        movie["score"]
                    ),
                    3,
                )

            results.append(movie_data)

        return results

    if isinstance(
        recommendations,
        pd.Series,
    ):
        movie_data = movie_to_dict(
            recommendations
        )

        if "score" in recommendations.index:
            movie_data["score"] = round(
                safe_float(
                    recommendations["score"]
                ),
                3,
            )

        return [movie_data]

    if isinstance(
        recommendations,
        (list, tuple),
    ):
        for movie in recommendations:
            movie_data = movie_to_dict(movie)

            if isinstance(movie, dict):
                if "score" in movie:
                    movie_data["score"] = round(
                        safe_float(
                            movie["score"]
                        ),
                        3,
                    )

            elif isinstance(movie, pd.Series):
                if "score" in movie.index:
                    movie_data["score"] = round(
                        safe_float(
                            movie["score"]
                        ),
                        3,
                    )

            results.append(movie_data)

    return results


@lru_cache(maxsize=V4_CACHE_SIZE)
def cached_v4_recommendations(
    title,
):
    return tuple(
        v4_recommend(
            title,
            top_n=20,
        )
        or []
    )


# ============================================================
# SEARCH ROUTE
# ============================================================

@app.route("/search")
def search():
    query = request.args.get(
        "q",
        "",
    ).strip()

    source = request.args.get(
        "source",
        "all",
    ).strip().lower()

    if not query:
        return jsonify([])

    normalized_query = normalize_search_text(
        query
    )

    if not normalized_query:
        return jsonify([])

    corrected_query = get_search_query(
        normalized_query
    )

    # --------------------------------------------------------
    # Local only
    # --------------------------------------------------------

    if source == "local":
        start = time.perf_counter()

        results = search_movies(
            corrected_query,
            SEARCH_LIMIT,
        )

        print(
            f"SEARCH LOCAL | '{query}' | "
            f"{time.perf_counter() - start:.4f}s | "
            f"{len(results)} results"
        )

        return jsonify(results)

    # --------------------------------------------------------
    # TMDB only
    # --------------------------------------------------------

    if source == "tmdb":
        results = cached_tmdb_search(
            corrected_query
        )

        output = [
            movie_to_dict(movie)
            for movie in results
        ]

        return jsonify(
            output[:SEARCH_LIMIT]
        )

    # --------------------------------------------------------
    # Local + TMDB
    # --------------------------------------------------------

    local_results = search_movies(
        corrected_query,
        SEARCH_LIMIT,
    )

    combined = []
    seen = set()

    for movie in local_results:
        title_key = make_match_key(
            movie.get(
                "title",
                "",
            )
        )

        media_type = movie.get(
            "media_type",
            "movie",
        )

        key = (
            title_key,
            media_type,
            "LOCAL",
        )

        if key in seen:
            continue

        seen.add(key)
        combined.append(movie)

    if len(corrected_query) >= TMDB_MIN_CHARS:
        tmdb_results = cached_tmdb_search(
            corrected_query
        )

        for raw_movie in tmdb_results:
            movie = movie_to_dict(
                raw_movie
            )

            title_key = make_match_key(
                movie.get(
                    "title",
                    "",
                )
            )

            media_type = movie.get(
                "media_type",
                "movie",
            )

            tmdb_id = safe_string(
                movie.get(
                    "tmdb_id",
                    "",
                )
            )

            key = (
                title_key,
                media_type,
                tmdb_id,
            )

            if key in seen:
                continue

            seen.add(key)
            combined.append(movie)

    # --------------------------------------------------------
    # Smart ranking
    # --------------------------------------------------------

    query_key = make_match_key(
        corrected_query
    )

    def ranking(movie):
        title = normalize_search_text(
            movie.get(
                "title",
                "",
            )
        )

        title_key = make_match_key(title)

        if title == normalized_query:
            score = 2_100_000

        elif title == corrected_query:
            score = 2_050_000

        elif title_key == query_key:
            score = 2_000_000

        elif title.startswith(
            normalized_query
        ):
            score = 1_000_000

        elif any(
            word.startswith(
                normalized_query
            )
            for word in title.split()
        ):
            score = 800_000

        elif normalized_query in title:
            score = 600_000

        else:
            similarity = SequenceMatcher(
                None,
                corrected_query,
                title,
            ).ratio()

            score = (
                300_000
                + similarity * 100_000
                if similarity >= 0.78
                else 0
            )

        if movie.get("source") == "TMDB":
            score += 500

        score += min(
            safe_float(
                movie.get(
                    "popularity",
                    0,
                )
            ),
            100,
        )

        score += (
            safe_float(
                movie.get(
                    "rating",
                    0,
                )
            )
            * 2
        )

        return score

    combined.sort(
        key=ranking,
        reverse=True,
    )

    return jsonify(
        combined[:SEARCH_LIMIT]
    )


# ============================================================
# HOME ROUTE
# ============================================================

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
        home_sections=HOME_SECTIONS,
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

    # --------------------------------------------------------
    # Request values
    # --------------------------------------------------------

    title = request.args.get(
        "movie",
        "",
    ).strip()

    selected_media_type = request.args.get(
        "media_type",
        "",
    ).strip().lower()

    selected_source = request.args.get(
        "source",
        "",
    ).strip().upper()

    selected_tmdb_id = request.args.get(
        "tmdb_id",
        "",
    ).strip()

    if request.method == "POST":
        title = (
            request.form.get("movie")
            or request.form.get("title")
            or title
        ).strip()

        selected_media_type = (
            request.form.get(
                "media_type",
                selected_media_type,
            )
            .strip()
            .lower()
        )

        selected_source = (
            request.form.get(
                "source",
                selected_source,
            )
            .strip()
            .upper()
        )

        selected_tmdb_id = (
            request.form.get(
                "tmdb_id",
                selected_tmdb_id,
            )
            .strip()
        )

    if not title:
        return render_template(
            "index.html",
            selected_movie=None,
            recommendations=[],
            search_results=[],
            selected_media_type=None,
            error=(
                "Please enter a movie or TV series name."
            ),
            notice=None,
            home_sections=HOME_SECTIONS,
        )

    # --------------------------------------------------------
    # Explicit TMDB selection
    # --------------------------------------------------------
    # This prevents an identically named local movie from
    # replacing the user's selected TMDB movie / TV result.
    # --------------------------------------------------------

    if (
        selected_source == "TMDB"
        and selected_media_type in {
            "movie",
            "tv",
        }
        and selected_tmdb_id
    ):
        tmdb_results = cached_tmdb_search(
            normalize_search_text(title)
        )

        selected_tmdb = None

        for movie in tmdb_results:
            movie_id = safe_string(
                movie.get(
                    "tmdb_id",
                    movie.get(
                        "id",
                        "",
                    ),
                )
            )

            if movie_id == str(
                selected_tmdb_id
            ):
                selected_tmdb = movie
                break

        if selected_tmdb is not None:
            selected_movie = movie_to_dict(
                selected_tmdb
            )

            selected_movie["source"] = "TMDB"
            selected_movie[
                "media_type"
            ] = selected_media_type

            if selected_media_type == "tv":
                return render_template(
                    "index.html",
                    selected_movie=selected_movie,
                    recommendations=[],
                    search_results=[],
                    selected_media_type="tv",
                    error=None,
                    notice=(
                        "This is a TV series. "
                        "The V4 recommender currently "
                        "supports movies only."
                    ),
                    home_sections=HOME_SECTIONS,
                )

            tmdb_id = selected_movie.get(
                "tmdb_id",
                "",
            )

            recommendations = [
                movie_to_dict(movie)
                for movie in smart_tmdb_recommendations(
                    str(tmdb_id),
                    20,
                )
            ]

            return render_template(
                "index.html",
                selected_movie=selected_movie,
                recommendations=recommendations,
                search_results=[],
                selected_media_type="movie",
                error=None,
                notice=None,
                home_sections=HOME_SECTIONS,
            )

    # --------------------------------------------------------
    # Local movie selection
    # --------------------------------------------------------

    selected = None

    local_results = search_movies(
        title,
        limit=5,
    )

    normalized_title = normalize_search_text(
        title
    )

    for movie in local_results:
        if (
            normalize_search_text(
                movie.get(
                    "title",
                    "",
                )
            )
            == normalized_title
        ):
            selected = movie
            break

    if selected is None and local_results:
        selected = local_results[0]

    # --------------------------------------------------------
    # Local recommendation
    # --------------------------------------------------------

    if selected is not None:
        selected_movie = movie_to_dict(
            selected
        )
        selected_movie["source"] = "LOCAL"
        selected_movie["media_type"] = "movie"

        try:
            recommendations = prepare_recommendations(
                cached_v4_recommendations(
                    selected_movie["title"]
                )
            )

        except Exception as exc:
            print(
                "V4 recommendation error:",
                repr(exc),
            )
            recommendations = []

        total_time = (
            time.perf_counter()
            - request_start
        )

        print(
            "Local movie:",
            selected_movie["title"],
        )
        print(
            "Recommendations:",
            len(recommendations),
        )
        print(
            f"TOTAL REQUEST TIME: "
            f"{total_time:.4f}s"
        )

        return render_template(
            "index.html",
            selected_movie=selected_movie,
            recommendations=recommendations,
            search_results=[],
            selected_media_type="movie",
            error=None,
            notice=None,
            home_sections=HOME_SECTIONS,
        )

    # --------------------------------------------------------
    # Local not found -> TMDB
    # --------------------------------------------------------

    tmdb_results = cached_tmdb_search(
        normalize_search_text(title)
    )

    if not tmdb_results:
        return render_template(
            "index.html",
            selected_movie=None,
            recommendations=[],
            search_results=[],
            selected_media_type=None,
            error=(
                f"'{title}' was not found "
                "in the local dataset or TMDB."
            ),
            notice=None,
            home_sections=HOME_SECTIONS,
        )

    # --------------------------------------------------------
    # Prefer exact TMDB title
    # --------------------------------------------------------

    exact_movie = None
    exact_tv = None

    for movie in tmdb_results:
        candidate = normalize_search_text(
            movie.get(
                "title",
                movie.get(
                    "name",
                    "",
                ),
            )
        )

        if candidate == normalize_search_text(
            title
        ):
            if movie.get(
                "media_type"
            ) == "tv":
                exact_tv = movie
            else:
                exact_movie = movie

            break

    selected_tmdb = (
        exact_tv
        or exact_movie
    )

    if selected_tmdb is None:
        for movie in tmdb_results:
            if movie.get(
                "media_type"
            ) == "movie":
                selected_tmdb = movie
                break

    if selected_tmdb is None:
        selected_tmdb = tmdb_results[0]

    selected_movie = movie_to_dict(
        selected_tmdb
    )

    selected_movie["source"] = "TMDB"

    media_type = selected_tmdb.get(
        "media_type",
        "movie",
    )

    selected_movie["media_type"] = (
        "tv"
        if media_type == "tv"
        else "movie"
    )

    if media_type == "tv":
        return render_template(
            "index.html",
            selected_movie=selected_movie,
            recommendations=[],
            search_results=[],
            selected_media_type="tv",
            error=None,
            notice=(
                "This is a TV series. "
                "The V4 recommender currently "
                "supports movies only."
            ),
            home_sections=HOME_SECTIONS,
        )

    tmdb_id = selected_movie.get(
        "tmdb_id",
        "",
    )

    recommendations = [
                movie_to_dict(movie)
                for movie in smart_tmdb_recommendations(
                    str(tmdb_id),
                    20,
                )
            ]

    total_time = (
        time.perf_counter()
        - request_start
    )

    print(
        "TMDB movie:",
        selected_movie["title"],
    )
    print(
        "Recommendations:",
        len(recommendations),
    )
    print(
        f"TOTAL REQUEST TIME: "
        f"{total_time:.4f}s"
    )

    return render_template(
        "index.html",
        selected_movie=selected_movie,
        recommendations=recommendations,
        search_results=[],
        selected_media_type="movie",
        error=None,
        notice=None,
        home_sections=HOME_SECTIONS,
    )


# ============================================================
# HEALTH ENDPOINT
# ============================================================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "movies_loaded": int(
            len(df)
        ),
        "search_index": int(
            len(SEARCH_RECORDS)
        ),
        "home_sections": {
            name: len(
                HOME_SECTIONS[name]
            )
            for name in (
                "trending",
                "recent",
                "mystery",
                "bollywood",
                "south",
                "english",
                "animation",
            )
        },
        "cache": {
            "local_search": (
                _local_search_cached
                .cache_info()
                ._asdict()
            ),
            "v4": (
                cached_v4_recommendations
                .cache_info()
                ._asdict()
            ),
            "tmdb_search": (
                cached_tmdb_search
                .cache_info()
                ._asdict()
            ),
            "tmdb_recommendations": (
                cached_tmdb_recommendations
                .cache_info()
                ._asdict()
            ),
            "tmdb_discovery": (
                discover_2026_movies
                .cache_info()
                ._asdict()
            ),
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
    print(
        f"Dataset       : {len(df):,} movies"
    )
    print(
        f"Search index  : {len(SEARCH_RECORDS):,} titles"
    )
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
