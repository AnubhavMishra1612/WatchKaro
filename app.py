# ============================================================
# MOVIE RECOMMENDER WEB APP
# ============================================================
#
# V5 submission-ready application version
#
# Main features
# ------------------------------------------------------------
# - Fast local autocomplete over the 78k+ movie dataset
# - Exact / tolerant / prefix / word / contains / fuzzy search
# - Movie + TV search through TMDB
# - Common spelling corrections (Bahubali, Spiderman, etc.)
# - V4 local movie recommender
# - TMDB recommendations for TMDB-only movies
# - Correct TV-series handling
# - Human-readable language names
# - Live 2026 homepage movies from TMDB
# - Recent releases section
# - Mystery & Thriller
# - Bollywood / Hindi
# - South Indian cinema
# - English cinema
# - Safe Pandas -> JSON conversion
# - Request/recommendation caching
# - Performance logs
#
# IMPORTANT
# ------------------------------------------------------------
# final_recommender.py is intentionally NOT modified.
# ============================================================


# ============================================================
# IMPORTS
# ============================================================

import heapq
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from bisect import bisect_left
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache

import pandas as pd
from flask import Flask, jsonify, render_template, request

from final_recommender import recommend as v4_recommend

from tmdb_api import (
    search_movies as tmdb_search_movies,
    get_movie_recommendations,
    get_movie_details,
    get_movie_similar,
    get_person_movie_credits,
    get_poster_url,
    get_2026_movies,
)


# ============================================================
# APPLICATION
# ============================================================

app = Flask(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

DATA_PATH = r"C:\movie-recommender\data\clean_movies.csv"

SEARCH_LIMIT = 10
HOME_SECTION_LIMIT = 10

TMDB_MIN_CHARS = 3

SEARCH_CACHE_SIZE = 512
V4_CACHE_SIZE = 256
TMDB_SEARCH_CACHE_SIZE = 256
TMDB_RECOMMENDATION_CACHE_SIZE = 256

# V5 ranking / diversity configuration
V5_CACHE_SIZE = 128
V5_DETAILS_CACHE_SIZE = 256
V5_PERSON_CACHE_SIZE = 256
V5_TOP_ACTORS = 5
V5_ACTOR_FILMOGRAPHY_LIMIT = 25
V5_DIRECTOR_FILMOGRAPHY_LIMIT = 30
V5_ACTOR_LIMIT_PRIMARY = 4
V5_ACTOR_LIMIT_FALLBACK = 5


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


def get_language_name(code):
    code = safe_string(code).strip().lower()
    if not code:
        return "Unknown"
    return LANGUAGE_NAMES.get(code, code.upper())


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
        value = float(value)
        if pd.isna(value):
            return default
        return value
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        value = int(float(value))
        return value
    except Exception:
        return default


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
    raise ValueError("Required dataset column missing: title")


# ============================================================
# NORMALIZE SEARCH TEXT
# ============================================================

def normalize_search_text(text):
    text = safe_string(text).lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


# ============================================================
# TOLERANT TITLE KEY
# ============================================================

def make_match_key(text):
    """Collapse repeated vowels so common spellings match."""
    text = normalize_search_text(text)
    words = []
    for word in text.split():
        word = re.sub(r"([aeiou])\1+", r"\1", word)
        words.append(word)
    return " ".join(words)


# ============================================================
# SEARCH ALIASES
# ============================================================

SEARCH_ALIASES = {
    "babubali": "baahubali",
    "bahubali": "baahubali",
    "babubali the beginning": "baahubali the beginning",
    "bahubali the beginning": "baahubali the beginning",
    "babubali 2": "baahubali 2",
    "bahubali 2": "baahubali 2",
    "spiderman": "spider man",
    "spidar man": "spider man",
    "interstelar": "interstellar",
    "intersteller": "interstellar",
}


def get_search_query(query):
    normalized = normalize_search_text(query)
    return SEARCH_ALIASES.get(normalized, normalized)


# ============================================================
# BUILD LOCAL SEARCH INDEX ONCE
# ============================================================

print()
print("Building fast search index...")

index_start = time.perf_counter()


df["search_title"] = (
    df["title"]
    .fillna("")
    .astype(str)
    .map(normalize_search_text)
)


if "popularity" in df.columns:
    df["_search_popularity"] = pd.to_numeric(
        df["popularity"], errors="coerce"
    ).fillna(0.0)
else:
    df["_search_popularity"] = 0.0


if "vote_count" in df.columns:
    df["_search_votes"] = pd.to_numeric(
        df["vote_count"], errors="coerce"
    ).fillna(0.0)
else:
    df["_search_votes"] = 0.0


# ------------------------------------------------------------
# Exact title -> row indexes
# ------------------------------------------------------------

EXACT_INDEX = {}

for idx, normalized_title in enumerate(df["search_title"].tolist()):
    if not normalized_title:
        continue
    EXACT_INDEX.setdefault(normalized_title, []).append(idx)


# ------------------------------------------------------------
# Tolerant title -> row indexes
# ------------------------------------------------------------

MATCH_KEY_INDEX = {}

for idx, normalized_title in enumerate(df["search_title"].tolist()):
    if not normalized_title:
        continue
    key = make_match_key(normalized_title)
    if key:
        MATCH_KEY_INDEX.setdefault(key, []).append(idx)


# ------------------------------------------------------------
# Sorted title records for prefix lookup
# ------------------------------------------------------------

SEARCH_RECORDS = [
    (normalized_title, idx)
    for idx, normalized_title in enumerate(
        df["search_title"].tolist()
    )
    if normalized_title
]

SEARCH_RECORDS.sort(key=lambda item: item[0])

SORTED_SEARCH_TITLES = [
    item[0] for item in SEARCH_RECORDS
]


# ------------------------------------------------------------
# Small fuzzy buckets
# ------------------------------------------------------------

FUZZY_BUCKETS = {}

for normalized_title, idx in SEARCH_RECORDS:
    bucket = normalized_title[:2]
    FUZZY_BUCKETS.setdefault(bucket, []).append(
        (normalized_title, idx)
    )


index_time = time.perf_counter() - index_start

print(
    f"Search index ready : {len(SEARCH_RECORDS):,} titles"
)
print(
    f"Index build time   : {index_time:.2f} seconds"
)


# ============================================================
# MOVIE -> JSON-SAFE DICTIONARY
# ============================================================

def movie_to_dict(movie):
    if movie is None:
        return {}

    def get_value(*keys, default=""):
        for key in keys:
            try:
                if isinstance(movie, dict):
                    if key not in movie:
                        continue
                    value = movie[key]
                else:
                    if key not in movie.index:
                        continue
                    value = movie[key]

                if value is None:
                    continue

                if not isinstance(value, (list, tuple, dict)) and pd.isna(value):
                    continue

                return value
            except Exception:
                continue
        return default

    title = safe_string(
        get_value("title", "name", default="Unknown"),
        "Unknown",
    )

    release_date = safe_string(
        get_value(
            "release_date",
            "first_air_date",
            "year",
            default="",
        )
    )

    match = re.search(r"(19|20)\d{2}", release_date)
    year = match.group(0) if match else ""

    language_code = safe_string(
        get_value(
            "original_language",
            "language",
            default="",
        )
    )

    language = get_language_name(language_code)

    genres = get_value("genres", "genre", default="")

    if isinstance(genres, (list, tuple)):
        genres = ", ".join(
            safe_string(item) for item in genres
        )
    elif isinstance(genres, dict):
        genres = ", ".join(
            safe_string(value)
            for value in genres.values()
        )

    genres = safe_string(genres)

    rating = safe_float(
        get_value("vote_average", "rating", default=0)
    )

    votes = safe_int(
        get_value("vote_count", "votes", default=0)
    )

    popularity = safe_float(
        get_value("popularity", default=0)
    )

    overview = safe_string(
        get_value("overview", default="")
    )

    poster_path = safe_string(
        get_value(
            "poster_path",
            "poster",
            default="",
        )
    )

    poster = ""

    if poster_path:
        if poster_path.startswith(("http://", "https://")):
            poster = poster_path
        else:
            try:
                poster = get_poster_url(poster_path)
            except Exception:
                poster = ""

    raw_tmdb_id = get_value(
        "tmdb_id",
        "id",
        default="",
    )

    tmdb_id = ""

    try:
        if raw_tmdb_id is None or pd.isna(raw_tmdb_id):
            tmdb_id = ""
        elif isinstance(raw_tmdb_id, int):
            tmdb_id = int(raw_tmdb_id)
        elif isinstance(raw_tmdb_id, float):
            tmdb_id = int(raw_tmdb_id)
        else:
            text_id = str(raw_tmdb_id).strip()
            tmdb_id = int(text_id) if text_id.isdigit() else text_id
    except Exception:
        tmdb_id = safe_string(raw_tmdb_id)

    media_type = safe_string(
        get_value("media_type", default="movie")
    ).lower()

    if media_type not in {"movie", "tv"}:
        media_type = "movie"

    source = safe_string(
        get_value("source", default="LOCAL")
    ).upper()

    return {
        "title": title,
        "year": year,
        "release_date": release_date,
        "language": language,
        "language_code": language_code,
        "genres": genres,
        "rating": round(float(rating), 1),
        "votes": int(votes),
        "popularity": round(float(popularity), 2),
        "overview": overview,
        "poster": safe_string(poster),
        "poster_path": poster_path,
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "source": source,
    }


# ============================================================
# FAST LOCAL SEARCH
# ============================================================

@lru_cache(maxsize=SEARCH_CACHE_SIZE)
def _local_search_cached(normalized_query, limit):
    if not normalized_query:
        return tuple()

    matches = {}

    # Exact spelling
    for idx in EXACT_INDEX.get(normalized_query, []):
        matches[idx] = 2_000_000

    # Common spelling / repeated-vowel normalization
    query_key = make_match_key(normalized_query)
    for idx in MATCH_KEY_INDEX.get(query_key, []):
        matches.setdefault(idx, 1_900_000)

    # Prefix lookup
    start = bisect_left(
        SORTED_SEARCH_TITLES,
        normalized_query,
    )

    for position in range(start, len(SEARCH_RECORDS)):
        title_text, idx = SEARCH_RECORDS[position]

        if not title_text.startswith(normalized_query):
            break

        matches.setdefault(idx, 1_000_000)

    # Word-start matching
    query_words = normalized_query.split()

    for title_text, idx in SEARCH_RECORDS:
        if idx in matches:
            continue

        title_words = title_text.split()

        if any(
            title_word.startswith(query_word)
            for query_word in query_words
            for title_word in title_words
        ):
            matches[idx] = 800_000

    # Contains
    for title_text, idx in SEARCH_RECORDS:
        if idx in matches:
            continue

        if normalized_query in title_text:
            matches[idx] = 600_000

    # Strict fuzzy fallback only when nothing else matched.
    if not matches:
        bucket = normalized_query[:2]
        candidates = FUZZY_BUCKETS.get(bucket, [])
        fuzzy_results = []

        for title_text, idx in candidates:
            if abs(len(title_text) - len(normalized_query)) > 15:
                continue

            overall = SequenceMatcher(
                None,
                normalized_query,
                title_text,
            ).ratio()

            best_word = 0.0
            for word in title_text.split():
                word_score = SequenceMatcher(
                    None,
                    normalized_query,
                    word,
                ).ratio()
                best_word = max(best_word, word_score)

            similarity = max(overall, best_word)

            if similarity >= 0.78:
                fuzzy_results.append((similarity, idx))

        fuzzy_results.sort(reverse=True)

        for similarity, idx in fuzzy_results[:limit]:
            matches[idx] = 300_000 + similarity * 100_000

    if not matches:
        return tuple()

    def rank(item):
        idx, score = item
        popularity = safe_float(df.iloc[idx]["_search_popularity"])
        votes = safe_float(df.iloc[idx]["_search_votes"])
        return score, popularity, votes

    ranked = heapq.nlargest(
        limit,
        matches.items(),
        key=rank,
    )

    results = []

    for idx, _score in ranked:
        movie = movie_to_dict(df.iloc[idx])
        movie["source"] = "LOCAL"
        movie["media_type"] = "movie"
        results.append(movie)

    return tuple(results)


def search_movies(query, limit=SEARCH_LIMIT):
    if not query:
        return []

    normalized_query = normalize_search_text(query)

    if not normalized_query or len(normalized_query) < 2:
        return []

    # Use corrected query for known spelling mistakes.
    corrected_query = get_search_query(normalized_query)

    results = _local_search_cached(
        corrected_query,
        int(limit),
    )

    return [dict(movie) for movie in results]


# ============================================================
# FAST LOCAL MOVIE LOOKUP
# ============================================================

def find_movie(title):
    if not title:
        return None

    normalized_title = normalize_search_text(title)

    if not normalized_title:
        return None

    start_time = time.perf_counter()

    # Exact spelling first.
    exact_indices = EXACT_INDEX.get(normalized_title, [])

    if exact_indices:
        best_index = max(
            exact_indices,
            key=lambda idx: safe_float(
                df.iloc[idx]["_search_votes"]
            ),
        )

        print(
            f"find_movie(): "
            f"{time.perf_counter() - start_time:.4f} seconds"
        )

        return df.iloc[best_index]

    # Known alias / tolerant spelling.
    corrected_query = get_search_query(normalized_title)
    corrected_key = make_match_key(corrected_query)

    tolerant_indices = MATCH_KEY_INDEX.get(corrected_key, [])

    if tolerant_indices:
        best_index = max(
            tolerant_indices,
            key=lambda idx: safe_float(
                df.iloc[idx]["_search_votes"]
            ),
        )

        print(
            f"find_movie(): "
            f"{time.perf_counter() - start_time:.4f} seconds"
        )

        return df.iloc[best_index]

    # Final local search fallback.
    results = search_movies(
        corrected_query,
        limit=1,
    )

    if results:
        found_title = safe_string(
            results[0].get("title", "")
        )
        found_normalized = normalize_search_text(found_title)

        indices = EXACT_INDEX.get(found_normalized, [])

        if indices:
            best_index = max(
                indices,
                key=lambda idx: safe_float(
                    df.iloc[idx]["_search_votes"]
                ),
            )

            print(
                f"find_movie(): "
                f"{time.perf_counter() - start_time:.4f} seconds"
            )

            return df.iloc[best_index]

    print(
        f"find_movie(): "
        f"{time.perf_counter() - start_time:.4f} seconds"
    )

    return None


# ============================================================
# TMDB SEARCH CACHE
# ============================================================

@lru_cache(maxsize=TMDB_SEARCH_CACHE_SIZE)
def cached_tmdb_search(normalized_query):
    if not normalized_query:
        return tuple()

    corrected_query = get_search_query(normalized_query)

    if corrected_query != normalized_query:
        print(
            f"SEARCH CORRECTION: "
            f"{normalized_query} -> {corrected_query}"
        )

    try:
        results = tmdb_search_movies(
            corrected_query,
            page=1,
        )

        if not results:
            return tuple()

        cleaned = []

        for movie in results:
            if not isinstance(movie, dict):
                continue

            media_type = movie.get(
                "media_type",
                "movie",
            )

            if media_type not in {"movie", "tv"}:
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


# ============================================================
# TMDB RECOMMENDATION CACHE
# ============================================================

@lru_cache(maxsize=TMDB_RECOMMENDATION_CACHE_SIZE)
def cached_tmdb_recommendations(tmdb_id):
    if not tmdb_id:
        return tuple()

    try:
        results = get_movie_recommendations(
            int(tmdb_id),
            page=1,
        )

        if not results:
            return tuple()

        cleaned = []

        for movie in results:
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


# ============================================================
# TMDB MOVIE / TV -> DICT
# ============================================================

TMDB_GENRE_NAMES = {
    28: "Action",
    12: "Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Science Fiction",
    10770: "TV Movie",
    53: "Thriller",
    10752: "War",
    37: "Western",
}


def tmdb_movie_to_dict(movie):
    if not movie:
        return {}

    movie = dict(movie)

    title = (
        movie.get("title")
        or movie.get("name")
        or "Unknown"
    )

    release_date = safe_string(
        movie.get("release_date")
        or movie.get("first_air_date")
        or ""
    )

    match = re.search(r"(19|20)\d{2}", release_date)
    year = match.group(0) if match else ""

    language_code = safe_string(
        movie.get("original_language", "")
    )

    genres = movie.get("genres", "")

    if isinstance(genres, (list, tuple)):
        genres = ", ".join(
            safe_string(item.get("name", item))
            if isinstance(item, dict)
            else safe_string(item)
            for item in genres
        )

    genres = safe_string(genres)

    # TMDB search/recommendation responses often provide genre_ids
    # without the full genre objects. Use the official TMDB genre IDs
    # so the UI does not show "Genre unavailable".
    if not genres:
        genre_ids = movie.get("genre_ids", []) or []
        mapped_genres = []
        for genre_id in genre_ids:
            try:
                name = TMDB_GENRE_NAMES.get(int(genre_id), "")
            except Exception:
                name = ""
            if name and name not in mapped_genres:
                mapped_genres.append(name)
        genres = ", ".join(mapped_genres)

    rating = safe_float(
        movie.get("vote_average", 0)
    )

    votes = safe_int(
        movie.get("vote_count", 0)
    )

    popularity = safe_float(
        movie.get("popularity", 0)
    )

    overview = safe_string(
        movie.get("overview", "")
    )

    poster_path = safe_string(
        movie.get("poster_path", "")
    )

    poster = ""

    if poster_path:
        try:
            poster = get_poster_url(poster_path)
        except Exception:
            poster = ""

    raw_tmdb_id = movie.get(
        "tmdb_id",
        movie.get("id", ""),
    )

    tmdb_id = ""

    try:
        if raw_tmdb_id is None:
            tmdb_id = ""
        elif isinstance(raw_tmdb_id, int):
            tmdb_id = int(raw_tmdb_id)
        elif isinstance(raw_tmdb_id, float):
            tmdb_id = int(raw_tmdb_id)
        else:
            raw_id_text = str(raw_tmdb_id).strip()
            tmdb_id = (
                int(raw_id_text)
                if raw_id_text.isdigit()
                else raw_id_text
            )
    except Exception:
        tmdb_id = safe_string(raw_tmdb_id)

    media_type = safe_string(
        movie.get("media_type", "movie")
    ).lower()

    if media_type not in {"movie", "tv"}:
        media_type = "movie"

    return {
        "title": safe_string(title, "Unknown"),
        "year": year,
        "release_date": release_date,
        "language": get_language_name(language_code),
        "language_code": language_code,
        "genres": genres,
        "rating": round(float(rating), 1),
        "votes": int(votes),
        "popularity": round(float(popularity), 2),
        "overview": overview,
        "poster": safe_string(poster),
        "poster_path": poster_path,
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "source": "TMDB",
        "match_score": safe_int(movie.get("_v5_match_percent", 0)),
        "cast": safe_string(movie.get("_watchkaro_cast", "")),
        "director": safe_string(movie.get("_watchkaro_director", "")),
    }


# ============================================================
# PREPARE V4 RECOMMENDATIONS
# ============================================================

def prepare_recommendations(recommendations):
    results = []

    if recommendations is None:
        return results

    if isinstance(recommendations, pd.DataFrame):
        for _, movie in recommendations.iterrows():
            data = movie_to_dict(movie)
            if "score" in movie:
                data["score"] = round(
                    safe_float(movie["score"]),
                    3,
                )
            results.append(data)
        return results

    if isinstance(recommendations, pd.Series):
        data = movie_to_dict(recommendations)
        if "score" in recommendations:
            data["score"] = round(
                safe_float(recommendations["score"]),
                3,
            )
        results.append(data)
        return results

    if isinstance(recommendations, (list, tuple)):
        for movie in recommendations:
            data = movie_to_dict(movie)
            if isinstance(movie, dict) and "score" in movie:
                data["score"] = round(
                    safe_float(movie["score"]),
                    3,
                )
            results.append(data)
        return results

    return results


# ============================================================
# WATCHKARO V5 RECOMMENDER
# ============================================================
# V5 adds:
# - Indian-cinema language guard (prevents English leakage)
# - Actor-based candidate generation
# - Director-based candidate generation
# - Theme / genre / language ranking
# - Actor diversity control (max 4 normally, max 5 fallback)
# - V4 remains the fallback when a local movie has no TMDB ID
# ============================================================

INDIAN_LANGUAGES = {
    "hi", "te", "ta", "ml", "kn",
    "bn", "mr", "gu", "pa", "ur",
}

V5_THEME_TERMS = {
    "mythology": {
        "mythology", "myth", "hindu", "hinduism", "ramayana",
        "mahabharata", "hanuman", "ram", "krishna", "deity",
        "god", "gods", "divine", "devotional", "epic", "legend",
        "mythological", "ancient india",
    },
    "superhero": {
        "superhero", "marvel", "dc", "batman", "superman",
        "spider", "avenger", "vigilante", "comic",
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
        "family", "kid", "kids", "child", "children", "friendship",
        "animation", "cartoon",
    },
}


def _v5_words(text):
    return set(re.findall(r"[a-z0-9]+", safe_string(text).lower()))


@lru_cache(maxsize=V5_DETAILS_CACHE_SIZE)
def cached_v5_movie_details(tmdb_id):
    if not tmdb_id:
        return {}

    try:
        details = get_movie_details(int(tmdb_id))
        return details if isinstance(details, dict) else {}
    except Exception as exc:
        print("V5 TMDB details error:", repr(exc))
        return {}


def enrich_tmdb_movie_for_display(movie):
    """Add full TMDB genres, top cast and director for the selected movie."""
    if not isinstance(movie, dict):
        return movie

    enriched = dict(movie)
    tmdb_id = safe_string(
        enriched.get("tmdb_id", enriched.get("id", ""))
    )
    if not tmdb_id:
        return enriched

    details = cached_v5_movie_details(tmdb_id)
    if not details:
        return enriched

    # Full details contain the proper genre objects.
    if details.get("genres"):
        enriched["genres"] = details.get("genres")

    if details.get("overview"):
        enriched["overview"] = details.get("overview")

    if details.get("release_date"):
        enriched["release_date"] = details.get("release_date")

    if details.get("poster_path"):
        enriched["poster_path"] = details.get("poster_path")

    credits = details.get("credits") or {}
    cast = credits.get("cast", []) if isinstance(credits, dict) else []
    crew = credits.get("crew", []) if isinstance(credits, dict) else []

    cast_names = []
    for person in cast:
        if not isinstance(person, dict):
            continue
        name = safe_string(person.get("name", ""))
        if name and name not in cast_names:
            cast_names.append(name)
        if len(cast_names) >= 5:
            break

    director_names = []
    for person in crew:
        if not isinstance(person, dict):
            continue
        if person.get("job") != "Director":
            continue
        name = safe_string(person.get("name", ""))
        if name and name not in director_names:
            director_names.append(name)
        if len(director_names) >= 2:
            break

    enriched["cast"] = ", ".join(cast_names)
    enriched["director"] = ", ".join(director_names)

    # Keep the internal keys used by tmdb_movie_to_dict() as well.
    enriched["_watchkaro_cast"] = enriched["cast"]
    enriched["_watchkaro_director"] = enriched["director"]

    return enriched


@lru_cache(maxsize=V5_PERSON_CACHE_SIZE)
def cached_v5_person_credits(person_id, role="cast"):
    if not person_id:
        return tuple()

    try:
        results = get_person_movie_credits(
            int(person_id),
            role=role,
        )
        return tuple(
            item for item in (results or [])
            if isinstance(item, dict)
        )
    except Exception as exc:
        print(
            f"V5 {role} filmography error [{person_id}]:",
            repr(exc),
        )
        return tuple()


def _v5_extract_profile(details):
    genres = details.get("genres") or []
    genre_ids = {
        int(item.get("id"))
        for item in genres
        if isinstance(item, dict)
        and str(item.get("id", "")).isdigit()
    }
    genre_names = {
        safe_string(item.get("name", "")).lower()
        for item in genres
        if isinstance(item, dict)
        and safe_string(item.get("name", ""))
    }

    keywords_payload = details.get("keywords") or {}
    keywords = (
        keywords_payload.get("keywords", [])
        if isinstance(keywords_payload, dict)
        else []
    )
    keyword_names = {
        safe_string(item.get("name", "")).lower()
        for item in keywords
        if isinstance(item, dict)
        and safe_string(item.get("name", ""))
    }

    credits = details.get("credits") or {}
    cast = credits.get("cast", []) if isinstance(credits, dict) else []
    crew = credits.get("crew", []) if isinstance(credits, dict) else []

    actor_ids = []
    actor_names = []
    for person in cast:
        if not isinstance(person, dict):
            continue
        person_id = person.get("id")
        if str(person_id).isdigit():
            actor_ids.append(int(person_id))
            actor_names.append(safe_string(person.get("name", "")))
        if len(actor_ids) >= V5_TOP_ACTORS:
            break

    director_ids = []
    director_names = []
    for person in crew:
        if not isinstance(person, dict) or person.get("job") != "Director":
            continue
        person_id = person.get("id")
        if str(person_id).isdigit():
            director_ids.append(int(person_id))
            director_names.append(safe_string(person.get("name", "")))

    title = safe_string(details.get("title", ""))
    overview = safe_string(details.get("overview", ""))
    tagline = safe_string(details.get("tagline", ""))
    language = safe_string(details.get("original_language", "")).lower()

    profile_text = " ".join([
        title, overview, tagline,
        " ".join(keyword_names),
        " ".join(genre_names),
        " ".join(actor_names),
        " ".join(director_names),
    ])
    profile_words = _v5_words(profile_text)

    title_words = _v5_words(title)
    if title_words.intersection({
        "hanuman", "hanu", "ram", "krishna", "adipurush",
        "ramayana", "mahabharata",
    }):
        profile_words.update(V5_THEME_TERMS["mythology"])

    active_themes = []
    for theme, terms in V5_THEME_TERMS.items():
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
        "profile_words": profile_words,
        "active_themes": active_themes,
        "actor_ids": actor_ids,
        "actor_names": actor_names,
        "director_ids": director_ids,
        "director_names": director_names,
    }


def _v5_allowed_language(selected_language, candidate_language):
    selected_language = safe_string(selected_language).lower()
    candidate_language = safe_string(candidate_language).lower()

    if not selected_language or not candidate_language:
        return True

    # Indian-language movies stay inside Indian cinema.
    # This is the hard guard that prevents English/Hollywood leakage.
    if selected_language in INDIAN_LANGUAGES:
        return candidate_language in INDIAN_LANGUAGES

    return True


def _v5_language_score(selected_language, candidate_language):
    selected_language = safe_string(selected_language).lower()
    candidate_language = safe_string(candidate_language).lower()

    if not selected_language or not candidate_language:
        return 0.0
    if selected_language == candidate_language:
        return 30.0
    if selected_language in INDIAN_LANGUAGES and candidate_language in INDIAN_LANGUAGES:
        return 12.0
    return 0.0


@lru_cache(maxsize=V5_CACHE_SIZE)
def cached_v5_recommendations(tmdb_id, limit=20):
    details = cached_v5_movie_details(str(tmdb_id))
    if not details:
        return tuple()

    profile = _v5_extract_profile(details)
    selected_id = safe_string(details.get("id", tmdb_id))
    selected_title = normalize_search_text(profile["title"])

    candidates = {}

    def add_candidate(movie, source, actor_id=None, director_id=None):
        if not isinstance(movie, dict):
            return

        movie_id = safe_string(
            movie.get("id", movie.get("tmdb_id", ""))
        )
        if not movie_id or movie_id == selected_id:
            return

        entry = candidates.setdefault(
            movie_id,
            {
                "movie": dict(movie),
                "actor_ids": set(),
                "director_ids": set(),
                "sources": set(),
            },
        )

        entry["sources"].add(source)
        if actor_id:
            entry["actor_ids"].add(int(actor_id))
        if director_id:
            entry["director_ids"].add(int(director_id))

    # TMDB's own recommendation feed remains a useful candidate source.
    try:
        for movie in cached_tmdb_recommendations(str(tmdb_id))[:V5_BASE_RECOMMENDATIONS if 'V5_BASE_RECOMMENDATIONS' in globals() else 20]:
            add_candidate(movie, "tmdb_recommendation")
    except Exception as exc:
        print("V5 base recommendation error:", repr(exc))

    # Similar movies are a second source.
    try:
        for movie in (get_movie_similar(int(tmdb_id), page=1) or [])[:20]:
            add_candidate(movie, "tmdb_similar")
    except Exception as exc:
        print("V5 similar-movie error:", repr(exc))

    futures = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for actor_id in profile["actor_ids"][:V5_TOP_ACTORS]:
            futures[
                executor.submit(
                    cached_v5_person_credits,
                    int(actor_id),
                    "cast",
                )
            ] = ("actor", actor_id)

        for director_id in profile["director_ids"]:
            futures[
                executor.submit(
                    cached_v5_person_credits,
                    int(director_id),
                    "director",
                )
            ] = ("director", director_id)

        for future in as_completed(futures):
            role, person_id = futures[future]
            try:
                filmography = future.result() or tuple()
            except Exception as exc:
                print(
                    f"V5 filmography future error [{role}/{person_id}]:",
                    repr(exc),
                )
                filmography = tuple()

            if role == "actor":
                for movie in filmography[:V5_ACTOR_FILMOGRAPHY_LIMIT]:
                    add_candidate(
                        movie,
                        "actor_filmography",
                        actor_id=person_id,
                    )
            else:
                for movie in filmography[:V5_DIRECTOR_FILMOGRAPHY_LIMIT]:
                    add_candidate(
                        movie,
                        "director_filmography",
                        director_id=person_id,
                    )

    ranked = []
    strong_theme = bool(profile["active_themes"])

    for item in candidates.values():
        movie = item["movie"]
        title = safe_string(movie.get("title", ""))
        title_norm = normalize_search_text(title)
        if not title_norm or title_norm == selected_title:
            continue

        candidate_language = safe_string(
            movie.get("original_language", "")
        ).lower()

        if not _v5_allowed_language(profile["language"], candidate_language):
            continue

        candidate_words = _v5_words(
            " ".join([title, safe_string(movie.get("overview", ""))])
        )

        candidate_genres = {
            int(value)
            for value in (movie.get("genre_ids") or [])
            if str(value).isdigit()
        }
        genre_overlap = len(profile["genre_ids"].intersection(candidate_genres))
        genre_score = min(genre_overlap, 3) * 12.0
        language_score = _v5_language_score(
            profile["language"],
            candidate_language,
        )

        text_overlap = len(profile["profile_words"].intersection(candidate_words))
        text_score = min(text_overlap * 1.6, 16.0)

        theme_score = 0.0
        for theme in profile["active_themes"]:
            hits = len(V5_THEME_TERMS[theme].intersection(candidate_words))
            if hits:
                theme_score += min(10.0 + hits * 3.0, 22.0)

        theme_penalty = 10.0 if strong_theme and theme_score == 0 else 0.0

        actor_matches = set(item["actor_ids"])
        director_matches = set(item["director_ids"])
        actor_score = min(len(actor_matches), 2) * 17.0
        director_score = 35.0 if director_matches else 0.0

        source_score = 0.0
        if "actor_filmography" in item["sources"]:
            source_score += 6.0
        if "director_filmography" in item["sources"]:
            source_score += 8.0
        if "tmdb_recommendation" in item["sources"]:
            source_score += 4.0
        if "tmdb_similar" in item["sources"]:
            source_score += 3.0

        rating = safe_float(movie.get("vote_average", 0.0))
        popularity = min(safe_float(movie.get("popularity", 0.0)), 100.0)
        quality_score = (rating / 10.0) * 5.0 + (popularity / 100.0) * 2.5

        total = (
            genre_score
            + language_score
            + text_score
            + theme_score
            + actor_score
            + director_score
            + source_score
            + quality_score
            - theme_penalty
        )

        ranked.append((total, movie, actor_matches, director_matches))

    ranked.sort(key=lambda item: item[0], reverse=True)

    # Convert the internal ranking score into a relative 0-100%
    # Match Score for display. The ranking itself is unchanged.
    max_rank_score = max(
        (score for score, *_ in ranked),
        default=0.0,
    )

    if max_rank_score > 0:
        for score, movie, _actor_matches, _director_matches in ranked:
            movie["_v5_match_percent"] = round(
                max(0.0, min(100.0, (score / max_rank_score) * 100.0))
            )
    else:
        for _score, movie, _actor_matches, _director_matches in ranked:
            movie["_v5_match_percent"] = 0

    selected = []
    selected_ids = set()
    actor_counts = {}

    def fill(max_per_actor):
        for score, movie, actor_matches, director_matches in ranked:
            movie_id = safe_string(movie.get("id", movie.get("tmdb_id", "")))
            if not movie_id or movie_id in selected_ids:
                continue

            if any(
                actor_counts.get(actor_id, 0) >= max_per_actor
                for actor_id in actor_matches
            ):
                continue

            selected.append(movie)
            selected_ids.add(movie_id)

            for actor_id in actor_matches:
                actor_counts[actor_id] = actor_counts.get(actor_id, 0) + 1

            if len(selected) >= limit:
                break

    fill(V5_ACTOR_LIMIT_PRIMARY)
    if len(selected) < limit:
        fill(V5_ACTOR_LIMIT_FALLBACK)

    print()
    print("=" * 70)
    print("WATCHKARO V5 RECOMMENDER")
    print("Selected:", profile["title"])
    print("Language:", profile["language"])
    print("Actors:", ", ".join(profile["actor_names"][:5]))
    print("Director:", ", ".join(profile["director_names"][:3]))
    print("Themes:", ", ".join(profile["active_themes"]) or "None")
    print("Candidates:", len(candidates))
    print("Ranked after language filter:", len(ranked))
    print("Final recommendations:", len(selected))
    for number, movie in enumerate(selected[:10], 1):
        print(f"  {number:02d}. {movie.get('title', 'Unknown')}")
    print("=" * 70)

    return tuple(selected[:limit])


# ============================================================
# CACHED V4 RECOMMENDER
# ============================================================

@lru_cache(maxsize=V4_CACHE_SIZE)
def cached_v4_recommendations(title):
    print(f"V4 recommender: {title}")
    return v4_recommend(title, top_n=20)


# ============================================================
# HOME DATAFRAME HELPER
# ============================================================

def dataframe_to_home_movies(frame, limit=HOME_SECTION_LIMIT):
    if frame is None or frame.empty:
        return []

    work = frame.copy()

    work["_home_title"] = (
        work["title"]
        .fillna("")
        .astype(str)
        .map(normalize_search_text)
    )

    work = work[work["_home_title"] != ""]
    work = work.drop_duplicates("_home_title")
    work = work.head(limit)

    results = []

    for _, movie in work.iterrows():
        data = movie_to_dict(movie)
        data["source"] = "LOCAL"
        data["media_type"] = "movie"
        results.append(data)

    return results


# ============================================================
# LOCAL FALLBACK FOR 2026 MOVIES
# ============================================================

def get_local_2026_movies(work, limit=HOME_SECTION_LIMIT):
    if "_home_release_date" not in work.columns:
        return []

    recent_2026 = work[
        work["_home_release_date"].dt.year == 2026
    ].copy()

    if recent_2026.empty:
        return []

    recent_2026 = recent_2026.sort_values(
        [
            "_home_popularity",
            "_home_rating",
            "_home_votes",
        ],
        ascending=[False, False, False],
    )

    return dataframe_to_home_movies(
        recent_2026,
        limit,
    )


# ============================================================
# BUILD HOME SECTIONS
# ============================================================

def build_home_sections():
    """
    Build homepage data once when Flask starts.

    Trending & New — 2026 is sourced live from TMDB.
    Other discovery categories use the local dataset.
    """

    home = {
        "trending": [],
        "recent": [],
        "mystery": [],
        "bollywood": [],
        "south": [],
        "english": [],
    }

    work = df.copy()

    # --------------------------------------------------------
    # Numeric columns
    # --------------------------------------------------------

    work["_home_popularity"] = pd.to_numeric(
        work["popularity"] if "popularity" in work.columns else 0,
        errors="coerce",
    ).fillna(0)

    work["_home_votes"] = pd.to_numeric(
        work["vote_count"] if "vote_count" in work.columns else 0,
        errors="coerce",
    ).fillna(0)

    work["_home_rating"] = pd.to_numeric(
        work["vote_average"] if "vote_average" in work.columns else 0,
        errors="coerce",
    ).fillna(0)

    # --------------------------------------------------------
    # Release date
    # --------------------------------------------------------

    if "release_date" in work.columns:
        work["_home_release_date"] = pd.to_datetime(
            work["release_date"],
            errors="coerce",
        )
    else:
        work["_home_release_date"] = pd.NaT

    # --------------------------------------------------------
    # Adult filtering
    # --------------------------------------------------------

    if "adult" in work.columns:
        adult_values = (
            work["adult"]
            .astype(str)
            .str.lower()
        )
        work = work[adult_values != "true"]

    # ========================================================
    # 1. TRENDING & NEW — LIVE 2026 TMDB
    # ========================================================

    print()
    print("Loading live 2026 homepage movies from TMDB...")

    try:
        tmdb_2026 = get_2026_movies(page=1)

        home["trending"] = [
            tmdb_movie_to_dict(movie)
            for movie in tmdb_2026[:HOME_SECTION_LIMIT]
        ]

        print(
            "Live 2026 movies loaded:",
            len(home["trending"]),
        )

    except Exception as exc:
        print(
            "TMDB 2026 home section error:",
            repr(exc),
        )

        # Never fall back to 2022/2023 here.
        # Only show local 2026 movies.
        home["trending"] = get_local_2026_movies(
            work,
            HOME_SECTION_LIMIT,
        )

        print(
            "Local 2026 fallback movies:",
            len(home["trending"]),
        )

    # ========================================================
    # 2. RECENT RELEASES
    # ========================================================
    # Recent local releases from the dataset.
    # ========================================================

    today = pd.Timestamp(datetime.now().date())

    recent = work[
        work["_home_release_date"].notna()
        & (work["_home_release_date"] <= today)
    ].copy()

    recent = recent.sort_values(
        [
            "_home_release_date",
            "_home_popularity",
        ],
        ascending=[False, False],
    )

    home["recent"] = dataframe_to_home_movies(
        recent,
        HOME_SECTION_LIMIT,
    )

    # ========================================================
    # GENRE / LANGUAGE TEXT
    # ========================================================

    if "genres" in work.columns:
        genre_text = (
            work["genres"]
            .fillna("")
            .astype(str)
            .str.lower()
        )
    else:
        genre_text = pd.Series("", index=work.index)

    if "original_language" in work.columns:
        language_text = (
            work["original_language"]
            .fillna("")
            .astype(str)
            .str.lower()
        )
    else:
        language_text = pd.Series("", index=work.index)

    # ========================================================
    # 3. MYSTERY + THRILLER
    # ========================================================

    mystery_filter = (
        genre_text.str.contains("mystery", na=False)
        | genre_text.str.contains("thriller", na=False)
    )

    mystery = work[mystery_filter].copy()

    mystery = mystery.sort_values(
        [
            "_home_rating",
            "_home_votes",
            "_home_popularity",
        ],
        ascending=[False, False, False],
    )

    home["mystery"] = dataframe_to_home_movies(
        mystery,
        HOME_SECTION_LIMIT,
    )

    # ========================================================
    # 4. BOLLYWOOD / HINDI
    # ========================================================

    bollywood = work[
        language_text == "hi"
    ].copy()

    bollywood = bollywood.sort_values(
        [
            "_home_popularity",
            "_home_rating",
            "_home_votes",
        ],
        ascending=[False, False, False],
    )

    home["bollywood"] = dataframe_to_home_movies(
        bollywood,
        HOME_SECTION_LIMIT,
    )

    # ========================================================
    # 5. SOUTH INDIAN
    # ========================================================

    south = work[
        language_text.isin(
            ["ta", "te", "ml", "kn"]
        )
    ].copy()

    south = south.sort_values(
        [
            "_home_popularity",
            "_home_rating",
            "_home_votes",
        ],
        ascending=[False, False, False],
    )

    home["south"] = dataframe_to_home_movies(
        south,
        HOME_SECTION_LIMIT,
    )

    # ========================================================
    # 6. ENGLISH
    # ========================================================

    english = work[
        language_text == "en"
    ].copy()

    english = english.sort_values(
        [
            "_home_popularity",
            "_home_rating",
            "_home_votes",
        ],
        ascending=[False, False, False],
    )

    home["english"] = dataframe_to_home_movies(
        english,
        HOME_SECTION_LIMIT,
    )

    return home


# ============================================================
# BUILD HOMEPAGE DATA ONCE
# ============================================================

print()
print("Building home page sections...")

home_start = time.perf_counter()

HOME_SECTIONS = build_home_sections()

home_time = time.perf_counter() - home_start

print(
    f"Home sections ready in {home_time:.2f} seconds"
)
print(
    "Trending 2026:",
    len(HOME_SECTIONS["trending"]),
)
print(
    "Recent:",
    len(HOME_SECTIONS["recent"]),
)
print(
    "Mystery:",
    len(HOME_SECTIONS["mystery"]),
)
print(
    "Bollywood:",
    len(HOME_SECTIONS["bollywood"]),
)
print(
    "South Indian:",
    len(HOME_SECTIONS["south"]),
)
print(
    "English:",
    len(HOME_SECTIONS["english"]),
)
print("=" * 70)


# ============================================================
# SEARCH API
# ============================================================

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    source = request.args.get("source", "all").lower()

    if not query:
        return jsonify([])

    normalized_query = normalize_search_text(query)

    if not normalized_query:
        return jsonify([])

    corrected_query = get_search_query(normalized_query)

    # --------------------------------------------------------
    # LOCAL ONLY
    # --------------------------------------------------------

    if source == "local":
        start = time.perf_counter()

        results = search_movies(
            corrected_query,
            SEARCH_LIMIT,
        )

        elapsed = time.perf_counter() - start

        print(
            f"SEARCH LOCAL | '{query}' | "
            f"{elapsed:.4f}s | {len(results)} results"
        )

        return jsonify(results)

    # --------------------------------------------------------
    # TMDB ONLY
    # --------------------------------------------------------

    if source == "tmdb":
        results = cached_tmdb_search(
            corrected_query
        )

        output = [
            tmdb_movie_to_dict(movie)
            for movie in results
        ]

        return jsonify(output[:SEARCH_LIMIT])

    # --------------------------------------------------------
    # LOCAL + TMDB
    # --------------------------------------------------------

    local_results = search_movies(
        corrected_query,
        SEARCH_LIMIT,
    )

    combined = []
    seen = set()

    for movie in local_results:
        title_key = make_match_key(
            movie.get("title", "")
        )
        media_type = movie.get(
            "media_type",
            "movie",
        )
        key = (title_key, media_type)

        if key in seen:
            continue

        seen.add(key)
        combined.append(movie)

    if len(corrected_query) >= TMDB_MIN_CHARS:
        tmdb_results = cached_tmdb_search(
            corrected_query
        )

        for raw_movie in tmdb_results:
            movie = tmdb_movie_to_dict(
                raw_movie
            )

            title_key = make_match_key(
                movie.get("title", "")
            )
            media_type = movie.get(
                "media_type",
                "movie",
            )
            key = (title_key, media_type)

            if key in seen:
                continue

            seen.add(key)
            combined.append(movie)

    # --------------------------------------------------------
    # Smart ranking
    # --------------------------------------------------------

    query_key = make_match_key(corrected_query)

    def ranking(movie):
        title = normalize_search_text(
            movie.get("title", "")
        )
        title_key = make_match_key(title)

        if title == normalized_query:
            score = 2_100_000
        elif title == corrected_query:
            score = 2_050_000
        elif title_key == query_key:
            score = 2_000_000
        elif title.startswith(normalized_query):
            score = 1_000_000
        elif any(
            word.startswith(normalized_query)
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
                300_000 + similarity * 100_000
                if similarity >= 0.78
                else 0
            )

        # TMDB gets a tiny tie-breaker.
        if movie.get("source") == "TMDB":
            score += 500

        score += min(
            safe_float(movie.get("popularity", 0)),
            100,
        )

        score += (
            safe_float(movie.get("rating", 0)) * 2
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

    # ========================================================
    # READ REQUEST VALUES
    # ========================================================

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

    print()
    print("=" * 70)
    print("RECOMMENDATION REQUEST")
    print("Title:", title)
    print("Source:", selected_source or "AUTO")
    print("Media:", selected_media_type or "AUTO")
    print("TMDB ID:", selected_tmdb_id or "AUTO")
    print("=" * 70)

    if not title:
        return render_template(
            "index.html",
            selected_movie=None,
            recommendations=[],
            search_results=[],
            selected_media_type=None,
            error="Please enter a movie or TV series name.",
            notice=None,
            home_sections=HOME_SECTIONS,
        )

    # ========================================================
    # EXPLICIT TMDB SELECTION
    # ========================================================
    # This prevents an identically named local movie from
    # replacing a selected TMDB movie/TV result.
    # ========================================================

    if (
        selected_source == "TMDB"
        and selected_media_type in {"movie", "tv"}
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
                    movie.get("id", ""),
                )
            )

            if movie_id == str(selected_tmdb_id):
                selected_tmdb = movie
                break

        if selected_tmdb is not None:
            selected_tmdb = enrich_tmdb_movie_for_display(selected_tmdb)
            selected_movie = tmdb_movie_to_dict(
                selected_tmdb
            )

            selected_movie["source"] = "TMDB"
            selected_movie["media_type"] = selected_media_type

            # ----------------------------
            # TV
            # ----------------------------

            if selected_media_type == "tv":
                total_time = (
                    time.perf_counter()
                    - request_start
                )

                print(
                    "Explicit TMDB TV:",
                    selected_movie["title"],
                )
                print(
                    f"TOTAL REQUEST TIME: {total_time:.4f}s"
                )

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

            # ----------------------------
            # TMDB MOVIE
            # ----------------------------

            raw_recommendations = cached_v5_recommendations(
                str(selected_movie.get("tmdb_id", "")),
                20,
            )

            recommendations = [
                tmdb_movie_to_dict(movie)
                for movie in raw_recommendations
            ]

            total_time = (
                time.perf_counter()
                - request_start
            )

            print(
                "Explicit TMDB movie:",
                selected_movie["title"],
            )
            print(
                "TMDB recommendations:",
                len(recommendations),
            )
            print(
                f"TOTAL REQUEST TIME: {total_time:.4f}s"
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

    # ========================================================
    # LOCAL MOVIE
    # ========================================================

    selected = find_movie(title)

    if selected is not None:
        selected_title = safe_string(
            selected.get("title", title),
            title,
        )

        selected_movie = movie_to_dict(selected)
        local_tmdb_id = selected_movie.get("tmdb_id", "")

        if local_tmdb_id:
            selected_movie = enrich_tmdb_movie_for_display({
                **selected_movie,
                "tmdb_id": local_tmdb_id,
                "id": local_tmdb_id,
                "original_language": selected_movie.get(
                    "language_code",
                    "",
                ),
            })

        selected_movie["source"] = "LOCAL"
        selected_movie["media_type"] = "movie"

        recommendation_start = time.perf_counter()

        try:
            local_tmdb_id = selected_movie.get("tmdb_id", "")

            if local_tmdb_id:
                raw_v5 = cached_v5_recommendations(
                    str(local_tmdb_id),
                    20,
                )
                recommendations = [
                    tmdb_movie_to_dict(movie)
                    for movie in raw_v5
                ]
                if not recommendations:
                    raise RuntimeError("V5 returned no recommendations")
                print("Local movie recommendation path: V5")
            else:
                raise RuntimeError(
                    "Local movie has no TMDB ID; using V4 fallback"
                )

        except Exception as exc:
            print(
                "V5 unavailable; using V4 fallback:",
                repr(exc),
            )

            try:
                raw_recommendations = (
                    cached_v4_recommendations(
                        selected_title
                    )
                )
                recommendations = prepare_recommendations(
                    raw_recommendations
                )
            except Exception as fallback_exc:
                print(
                    "V4 recommendation error:",
                    repr(fallback_exc),
                )
                recommendations = []

        recommendation_time = (
            time.perf_counter()
            - recommendation_start
        )

        total_time = (
            time.perf_counter()
            - request_start
        )

        print(
            f"V4 recommendation time: "
            f"{recommendation_time:.4f}s"
        )
        print(
            "Recommendations:",
            len(recommendations),
        )
        print(
            f"TOTAL REQUEST TIME: {total_time:.4f}s"
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

    # ========================================================
    # LOCAL MISS -> TMDB
    # ========================================================

    normalized_query = normalize_search_text(title)

    tmdb_results = cached_tmdb_search(
        normalized_query
    )

    if not tmdb_results:
        return render_template(
            "index.html",
            selected_movie=None,
            recommendations=[],
            search_results=[],
            selected_media_type=None,
            error=(
                f"'{title}' was not found in the "
                "local dataset or TMDB."
            ),
            notice=None,
            home_sections=HOME_SECTIONS,
        )

    # Prefer an exact title match.
    exact_tv = None
    exact_movie = None

    for movie in tmdb_results:
        candidate = normalize_search_text(
            movie.get(
                "title",
                movie.get("name", ""),
            )
        )

        if candidate != normalized_query:
            continue

        if movie.get("media_type") == "tv":
            exact_tv = movie
        else:
            exact_movie = movie

    selected_tmdb = exact_tv or exact_movie

    # Otherwise prefer a movie, then the first result.
    if selected_tmdb is None:
        for movie in tmdb_results:
            if movie.get("media_type") == "movie":
                selected_tmdb = movie
                break

        if selected_tmdb is None:
            selected_tmdb = tmdb_results[0]

    # ========================================================
    # TMDB TV
    # ========================================================

    if selected_tmdb.get("media_type") == "tv":
        selected_movie = tmdb_movie_to_dict(
            selected_tmdb
        )

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

    # ========================================================
    # TMDB MOVIE
    # ========================================================

    selected_tmdb = enrich_tmdb_movie_for_display(selected_tmdb)
    selected_movie = tmdb_movie_to_dict(
        selected_tmdb
    )

    tmdb_id = selected_movie.get("tmdb_id", "")

    raw_recommendations = (
        cached_v5_recommendations(
            str(tmdb_id),
            20,
        )
        if tmdb_id
        else tuple()
    )

    recommendations = [
        tmdb_movie_to_dict(movie)
        for movie in raw_recommendations
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
        f"TOTAL REQUEST TIME: {total_time:.4f}s"
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
# HEALTH / DEBUG ENDPOINT
# ============================================================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "movies_loaded": int(len(df)),
        "search_index": int(len(SEARCH_RECORDS)),
        "home_sections": {
            "trending_2026": len(
                HOME_SECTIONS["trending"]
            ),
            "recent": len(
                HOME_SECTIONS["recent"]
            ),
            "mystery": len(
                HOME_SECTIONS["mystery"]
            ),
            "bollywood": len(
                HOME_SECTIONS["bollywood"]
            ),
            "south": len(
                HOME_SECTIONS["south"]
            ),
            "english": len(
                HOME_SECTIONS["english"]
            ),
        },
        "search_cache": (
            _local_search_cached
            .cache_info()
            ._asdict()
        ),
        "v4_cache": (
            cached_v4_recommendations
            .cache_info()
            ._asdict()
        ),
        "v5_cache": (
            cached_v5_recommendations
            .cache_info()
            ._asdict()
        ),
        "v5_details_cache": (
            cached_v5_movie_details
            .cache_info()
            ._asdict()
        ),
        "tmdb_search_cache": (
            cached_tmdb_search
            .cache_info()
            ._asdict()
        ),
        "tmdb_recommendation_cache": (
            cached_tmdb_recommendations
            .cache_info()
            ._asdict()
        ),
    })


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 70)
    print("WATCHKARO V5 MOVIE RECOMMENDER WEB APP")
    print("=" * 70)
    print(
        f"Dataset       : {len(df):,} movies"
    )
    print(
        f"Search index  : {len(SEARCH_RECORDS):,} titles"
    )
    print()
    print("Open:")
    print("http://127.0.0.1:5000/")
    print()
    print("Health:")
    print("http://127.0.0.1:5000/health")
    print()
    print("Press CTRL+C to stop.")
    print("=" * 70)

    app.run(
        debug=True,
        use_reloader=False,
        threaded=True,
    )
