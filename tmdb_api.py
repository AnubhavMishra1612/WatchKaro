# ============================================================
# WATCHKARO — TMDB API HELPER
# ============================================================
#
# TMDB API infrastructure for WatchKaro deployed on Render.
# Lightweight, cached, and fully compatible with existing features.
#
# Credentials are read strictly from environment variables:
#   - TMDB_ACCESS_TOKEN
#   - TMDB_API_KEY
#
# Never expose keys to client-side code or HTML.
# ============================================================

import os
import math
import time
from datetime import datetime
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# Language display mapping
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

# Standard TMDB Movie Genres
GENRE_MAP = {
    "28": "Action",
    "12": "Adventure",
    "16": "Animation",
    "35": "Comedy",
    "80": "Crime",
    "99": "Documentary",
    "18": "Drama",
    "10751": "Family",
    "14": "Fantasy",
    "36": "History",
    "27": "Horror",
    "10402": "Music",
    "9648": "Mystery",
    "10749": "Romance",
    "878": "Sci-Fi",
    "10770": "TV Movie",
    "53": "Thriller",
    "10752": "War",
    "37": "Western",
}

# Reverse genre name to ID mapping
GENRE_NAME_TO_ID = {v.lower(): k for k, v in GENRE_MAP.items()}


def _request(path, params=None, timeout=12):
    """Send an authenticated GET request to TMDB."""
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


def get_poster_url(poster_path, size="w500"):
    """Convert TMDB poster_path into a full image URL."""
    if not poster_path:
        return ""

    poster_path = str(poster_path).strip()
    if poster_path.startswith("http://") or poster_path.startswith("https://"):
        return poster_path

    if not poster_path.startswith("/"):
        poster_path = "/" + poster_path

    return f"https://image.tmdb.org/t/p/{size}{poster_path}"


def get_language_name(code):
    """Convert language code to readable string."""
    code = str(code or "").strip().lower()
    if not code:
        return "Unknown"
    return LANGUAGE_NAMES.get(code, code.upper())


def format_tmdb_movie(item):
    """Normalize a raw TMDB movie dict into standard WatchKaro format."""
    if not isinstance(item, dict):
        return {}

    title = item.get("title") or item.get("name") or "Unknown"
    release_date = item.get("release_date") or item.get("first_air_date") or ""
    year = release_date[:4] if release_date[:4].isdigit() else ""
    lang_code = (item.get("original_language") or "").lower()

    # Genres can be list of dicts or list of ids
    genre_names = []
    if "genres" in item and isinstance(item["genres"], list):
        for g in item["genres"]:
            if isinstance(g, dict) and g.get("name"):
                genre_names.append(g["name"])
    elif "genre_ids" in item and isinstance(item["genre_ids"], list):
        for gid in item["genre_ids"]:
            g_name = GENRE_MAP.get(str(gid))
            if g_name:
                genre_names.append(g_name)

    vote_avg = round(float(item.get("vote_average") or 0.0), 1)
    popularity = round(float(item.get("popularity") or 0.0), 1)
    vote_count = int(item.get("vote_count") or 0)

    # Cast and director if present in appended credits
    cast_names = []
    director_names = []
    credits = item.get("credits") or {}
    if isinstance(credits, dict):
        cast_list = credits.get("cast") or []
        crew_list = credits.get("crew") or []
        cast_names = [
            str(p.get("name", "")) for p in cast_list[:5]
            if isinstance(p, dict) and p.get("name")
        ]
        director_names = [
            str(p.get("name", "")) for p in crew_list
            if isinstance(p, dict)
            and str(p.get("job", "")).lower() == "director"
            and p.get("name")
        ][:2]

    return {
        "title": title,
        "name": title,
        "tmdb_id": str(item.get("id") or item.get("tmdb_id") or ""),
        "id": item.get("id"),
        "media_type": item.get("media_type") or "movie",
        "source": "TMDB",
        "poster": get_poster_url(item.get("poster_path") or item.get("poster")),
        "year": year,
        "release_date": release_date,
        "language": get_language_name(lang_code),
        "language_code": lang_code,
        "rating": vote_avg,
        "vote_average": vote_avg,
        "vote_count": vote_count,
        "popularity": popularity,
        "genres": ", ".join(genre_names),
        "genre_ids": item.get("genre_ids") or [g.get("id") for g in item.get("genres", []) if isinstance(g, dict)],
        "overview": str(item.get("overview") or "").strip(),
        "cast": ", ".join(cast_names),
        "director": ", ".join(director_names),
        "match_score": item.get("match_score"),
    }


# ============================================================
# CACHED BASIC TMDB REQUESTS
# ============================================================

@lru_cache(maxsize=128)
def search_movies(query, page=1):
    """Search TMDB for both movies and TV series."""
    query = str(query or "").strip()
    if not query:
        return []

    try:
        data = _request(
            "/search/multi",
            params={
                "query": query,
                "language": "en-US",
                "include_adult": "false",
                "page": int(page),
            },
        )
    except Exception as exc:
        print("TMDB search error:", exc)
        return []

    results = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        media_type = item.get("media_type")
        if media_type not in {"movie", "tv"}:
            continue
        cleaned = dict(item)
        if media_type == "tv" and not cleaned.get("title"):
            cleaned["title"] = cleaned.get("name", "")
        cleaned["tmdb_id"] = cleaned.get("id")
        results.append(cleaned)

    return results


@lru_cache(maxsize=128)
def get_movie_details(movie_id):
    """Get complete TMDB movie details plus keywords and credits."""
    if not movie_id:
        return {}

    try:
        return _request(
            f"/movie/{int(movie_id)}",
            params={
                "language": "en-US",
                "append_to_response": "keywords,credits",
            },
        )
    except Exception as exc:
        print(f"TMDB details error ({movie_id}):", exc)
        return {}


@lru_cache(maxsize=128)
def get_movie_credits(movie_id):
    """Return cast and crew credits for a TMDB movie."""
    if not movie_id:
        return {}

    try:
        return _request(
            f"/movie/{int(movie_id)}/credits",
            params={"language": "en-US"},
        )
    except Exception:
        return {}


@lru_cache(maxsize=64)
def get_person_movie_credits(person_id, role="cast"):
    """Return movie filmography entries for an actor or director."""
    if not person_id:
        return []

    try:
        data = _request(
            f"/person/{int(person_id)}/combined_credits",
            params={"language": "en-US"},
        )
    except Exception:
        return []

    role = str(role or "cast").lower().strip()
    source = data.get("cast", []) if role == "cast" else data.get("crew", [])

    results = []
    for item in source or []:
        if not isinstance(item, dict):
            continue
        if item.get("media_type") not in {None, "movie"}:
            continue
        if role == "director" and item.get("job") != "Director":
            continue

        movie = dict(item)
        movie["tmdb_id"] = movie.get("id")
        movie["media_type"] = "movie"
        results.append(movie)

    return results


@lru_cache(maxsize=64)
def get_movie_recommendations(movie_id, page=1):
    """Return TMDB's movie recommendation feed."""
    if not movie_id:
        return []

    try:
        data = _request(
            f"/movie/{int(movie_id)}/recommendations",
            params={"language": "en-US", "page": int(page)},
        )
        results = []
        for movie in data.get("results", []):
            if isinstance(movie, dict):
                m = dict(movie)
                m["tmdb_id"] = m.get("id")
                m["media_type"] = "movie"
                results.append(m)
        return results
    except Exception:
        return []


@lru_cache(maxsize=64)
def get_movie_similar(movie_id, page=1):
    """Return TMDB's similar-movie feed."""
    if not movie_id:
        return []

    try:
        data = _request(
            f"/movie/{int(movie_id)}/similar",
            params={"language": "en-US", "page": int(page)},
        )
        results = []
        for movie in data.get("results", []):
            if isinstance(movie, dict):
                m = dict(movie)
                m["tmdb_id"] = m.get("id")
                m["media_type"] = "movie"
                results.append(m)
        return results
    except Exception:
        return []


@lru_cache(maxsize=16)
def get_2026_movies(page=1):
    """Return released 2026 movies from TMDB."""
    today = datetime.now().date().isoformat()
    try:
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
        return [format_tmdb_movie(m) for m in data.get("results", []) if isinstance(m, dict)]
    except Exception as exc:
        print("2026 movies error:", exc)
        return []


@lru_cache(maxsize=32)
def get_2026_movies_by_filter(
    genre_ids="",
    original_language="",
    sort_by="popularity.desc",
    page=1,
    vote_count_gte=5,
):
    """Generic 2026 discover helper kept for compatibility."""
    today = datetime.now().date().isoformat()
    languages = [c.strip().lower() for c in str(original_language or "").split("|") if c.strip()]
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

        try:
            data = _request("/discover/movie", params=params)
            for movie in data.get("results", []):
                if isinstance(movie, dict):
                    all_results.append(movie)
        except Exception:
            continue

    seen = set()
    results = []
    for movie in all_results:
        movie_id = movie.get("id") or movie.get("tmdb_id")
        if movie_id in seen:
            continue
        seen.add(movie_id)
        results.append(format_tmdb_movie(movie))

    return results


# ============================================================
# FEATURE 1: EXPLORE / CATEGORY FILTER DISCOVERY
# ============================================================

# In-memory discovery cache with TTL to keep responses fast and free-tier safe
_EXPLORE_CACHE = {}
_EXPLORE_CACHE_TTL = 300  # 5 minutes


def discover_movies(
    preset="popular",
    year="all",
    language="all",
    genre="all",
    industry="all",
    page=1,
):
    """
    Explore movies via TMDB /discover/movie and /trending with multi-filter support.
    
    Filters:
    - preset: 'trending', 'popular', 'highest_rated', 'most_voted', 'new_releases'
    - year: 'all', '2026', '2025', '2024', '2023', '2022', '2020-2021', '2010s', '2000s', '1990s', 'classic'
    - language: 'all', 'en', 'hi', 'ta', 'te', 'ml', 'kn', 'ko', 'ja', 'es', 'fr', 'de'
    - genre: 'all' or numeric TMDB genre ID (e.g. '28')
    - industry: 'all', 'hollywood', 'bollywood', 'south_indian', 'asian', 'european'
    """
    cache_key = (str(preset), str(year), str(language), str(genre), str(industry), int(page))
    now = time.time()
    if cache_key in _EXPLORE_CACHE:
        cached_time, cached_data = _EXPLORE_CACHE[cache_key]
        if now - cached_time < _EXPLORE_CACHE_TTL:
            return cached_data

    today = datetime.now().date().isoformat()
    params = {
        "language": "en-US",
        "page": int(page),
        "include_adult": "false",
        "include_video": "false",
    }

    # 1. Preset / Sorting & Quality Baselines
    preset_lower = str(preset or "popular").lower().strip()
    if preset_lower == "trending":
        # Trending uses popularity descending with recent release date
        params["sort_by"] = "popularity.desc"
        params["vote_count.gte"] = 15
    elif preset_lower == "highest_rated":
        params["sort_by"] = "vote_average.desc"
        params["vote_count.gte"] = 150  # Prevent 1-vote 10/10 skew
    elif preset_lower == "most_voted":
        params["sort_by"] = "vote_count.desc"
    elif preset_lower == "new_releases":
        params["sort_by"] = "primary_release_date.desc"
        params["primary_release_date.lte"] = today
        params["vote_count.gte"] = 5
    else:  # popular
        params["sort_by"] = "popularity.desc"
        params["vote_count.gte"] = 20

    # 2. Year Filter
    year_str = str(year or "all").lower().strip()
    if year_str in {"2026", "2025", "2024", "2023", "2022"}:
        params["primary_release_year"] = int(year_str)
    elif year_str == "2020-2021":
        params["primary_release_date.gte"] = "2020-01-01"
        params["primary_release_date.lte"] = "2021-12-31"
    elif year_str == "2010s":
        params["primary_release_date.gte"] = "2010-01-01"
        params["primary_release_date.lte"] = "2019-12-31"
    elif year_str == "2000s":
        params["primary_release_date.gte"] = "2000-01-01"
        params["primary_release_date.lte"] = "2009-12-31"
    elif year_str == "1990s":
        params["primary_release_date.gte"] = "1990-01-01"
        params["primary_release_date.lte"] = "1999-12-31"
    elif year_str == "classic":
        params["primary_release_date.lte"] = "1989-12-31"

    # 3. Genre Filter
    genre_str = str(genre or "all").strip()
    if genre_str != "all" and genre_str.isdigit():
        params["with_genres"] = genre_str

    # 4. Language & Industry / Region Filter
    lang_str = str(language or "all").strip().lower()
    ind_str = str(industry or "all").strip().lower()

    # Determine languages to query
    languages_to_query = []
    if lang_str != "all":
        languages_to_query = [lang_str]
    elif ind_str == "hollywood":
        languages_to_query = ["en"]
    elif ind_str == "bollywood":
        languages_to_query = ["hi"]
    elif ind_str == "south_indian":
        languages_to_query = ["ta", "te", "ml", "kn"]
    elif ind_str == "asian":
        languages_to_query = ["ko", "ja", "zh"]
    elif ind_str == "european":
        languages_to_query = ["fr", "es", "de", "it"]
    else:
        languages_to_query = [""]

    all_raw_movies = []

    # If South Indian or Asian, dispatch parallel requests per language to ensure variety
    if len(languages_to_query) > 1:
        def fetch_lang(code):
            p = dict(params)
            p["with_original_language"] = code
            try:
                res = _request("/discover/movie", params=p, timeout=8)
                return res.get("results", []) or []
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=min(4, len(languages_to_query))) as executor:
            future_to_code = {executor.submit(fetch_lang, code): code for code in languages_to_query}
            for future in as_completed(future_to_code):
                try:
                    all_raw_movies.extend(future.result())
                except Exception:
                    pass
    else:
        code = languages_to_query[0]
        p = dict(params)
        if code:
            p["with_original_language"] = code
        try:
            res = _request("/discover/movie", params=p, timeout=12)
            all_raw_movies = res.get("results", []) or []
        except Exception as exc:
            print("Discover query error:", exc)
            all_raw_movies = []

    # Deduplicate and format
    seen = set()
    cleaned = []
    for item in all_raw_movies:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        formatted = format_tmdb_movie(item)
        if formatted.get("poster"):  # Prefer movies with posters for explore grid
            cleaned.append(formatted)

    # Sort based on preset
    if preset_lower == "highest_rated":
        cleaned.sort(key=lambda x: (x.get("vote_average", 0), x.get("vote_count", 0)), reverse=True)
    elif preset_lower == "most_voted":
        cleaned.sort(key=lambda x: x.get("vote_count", 0), reverse=True)
    elif preset_lower == "new_releases":
        cleaned.sort(key=lambda x: x.get("release_date", ""), reverse=True)
    else:
        cleaned.sort(key=lambda x: x.get("popularity", 0), reverse=True)

    result = cleaned[:30]
    _EXPLORE_CACHE[cache_key] = (now, result)
    return result


# ============================================================
# FEATURE 2: FIND MY MOVIE — RULE-BASED RECOMMENDATION QUIZ
# ============================================================

def get_quiz_recommendations(answers):
    """
    Generate 5–10 rule-based movie recommendations from 6–7 actual user answers.
    
    Answers dictionary:
    - genre: (e.g. 'Action & Adventure', 'Comedy', 'Drama', etc.)
    - language: (e.g. 'Hindi', 'English', 'South Indian', 'Korean', 'Japanese', 'Any')
    - mood: (e.g. 'feel_good', 'thrill', 'emotional', 'mind_bending', 'dark', 'chill')
    - year_era: (e.g. '2025_2026', '2020_2024', '2010s', 'classics', 'any')
    - popularity_pref: (e.g. 'blockbuster', 'acclaimed', 'hidden_gem', 'any')
    - length_pref: (e.g. 'short', 'medium', 'epic', 'any')
    - industry: (e.g. 'hollywood', 'bollywood', 'south_indian', 'asian', 'european', 'any')
    
    RULE-BASED:
    No generative AI. No fake recommendation scores.
    Match percentage is calculated strictly from actual criteria and capped at 100%.
    """
    genre_input = str(answers.get("genre") or "").strip().lower()
    lang_input = str(answers.get("language") or "").strip().lower()
    mood = str(answers.get("mood") or "feel_good").strip().lower()
    era = str(answers.get("year_era") or "any").strip().lower()
    pop_pref = str(answers.get("popularity_pref") or "acclaimed").strip().lower()
    length_pref = str(answers.get("length_pref") or "any").strip().lower()
    industry = str(answers.get("industry") or "any").strip().lower()

    # Map genre selection to TMDB IDs
    primary_genre_ids = []
    if "action" in genre_input or "adventure" in genre_input:
        primary_genre_ids.extend([28, 12])
    elif "comedy" in genre_input:
        primary_genre_ids.append(35)
    elif "drama" in genre_input:
        primary_genre_ids.append(18)
    elif "horror" in genre_input:
        primary_genre_ids.append(27)
    elif "sci" in genre_input or "fantasy" in genre_input:
        primary_genre_ids.extend([878, 14])
    elif "romance" in genre_input:
        primary_genre_ids.append(10749)
    elif "thriller" in genre_input or "mystery" in genre_input:
        primary_genre_ids.extend([53, 9648])
    elif "animation" in genre_input or "family" in genre_input:
        primary_genre_ids.extend([16, 10751])
    elif "crime" in genre_input:
        primary_genre_ids.append(80)

    # Mood-aligned secondary genre IDs
    mood_genres = []
    if mood == "feel_good":
        mood_genres = [35, 10751, 12]
    elif mood == "thrill":
        mood_genres = [28, 53, 80]
    elif mood == "emotional":
        mood_genres = [18, 10749]
    elif mood == "mind_bending":
        mood_genres = [878, 9648, 53]
    elif mood == "dark":
        mood_genres = [80, 27, 53]
    elif mood == "chill":
        mood_genres = [16, 35, 12]

    # Map language & industry
    target_languages = []
    if "hindi" in lang_input or industry == "bollywood":
        target_languages = ["hi"]
    elif "south" in lang_input or industry == "south_indian":
        target_languages = ["ta", "te", "ml", "kn"]
    elif "korean" in lang_input:
        target_languages = ["ko"]
    elif "japanese" in lang_input:
        target_languages = ["ja"]
    elif "spanish" in lang_input:
        target_languages = ["es"]
    elif "french" in lang_input:
        target_languages = ["fr"]
    elif "english" in lang_input or industry == "hollywood":
        target_languages = ["en"]
    elif industry == "asian":
        target_languages = ["ko", "ja", "zh"]
    elif industry == "european":
        target_languages = ["fr", "es", "de", "it"]
    else:
        target_languages = ["en", "hi"]

    # Discover candidate pool
    discover_params = {
        "language": "en-US",
        "include_adult": "false",
        "include_video": "false",
        "page": 1,
    }

    if primary_genre_ids:
        discover_params["with_genres"] = ",".join(str(g) for g in primary_genre_ids[:2])

    if era == "2025_2026":
        discover_params["primary_release_date.gte"] = "2025-01-01"
    elif era == "2020_2024":
        discover_params["primary_release_date.gte"] = "2020-01-01"
        discover_params["primary_release_date.lte"] = "2024-12-31"
    elif era == "2010s":
        discover_params["primary_release_date.gte"] = "2010-01-01"
        discover_params["primary_release_date.lte"] = "2019-12-31"
    elif era == "classics":
        discover_params["primary_release_date.lte"] = "1999-12-31"

    # Runtime constraints
    if length_pref == "short":
        discover_params["with_runtime.lte"] = 100
    elif length_pref == "medium":
        discover_params["with_runtime.gte"] = 100
        discover_params["with_runtime.lte"] = 145
    elif length_pref == "epic":
        discover_params["with_runtime.gte"] = 145

    # Popularity sorting
    if pop_pref == "blockbuster":
        discover_params["sort_by"] = "popularity.desc"
        discover_params["vote_count.gte"] = 100
    elif pop_pref == "hidden_gem":
        discover_params["sort_by"] = "vote_average.desc"
        discover_params["vote_count.gte"] = 30
        discover_params["vote_average.gte"] = 7.0
    else:  # acclaimed or any
        discover_params["sort_by"] = "vote_average.desc"
        discover_params["vote_count.gte"] = 80

    candidates = []

    def fetch_candidates_for_lang(lang_code):
        p = dict(discover_params)
        if lang_code:
            p["with_original_language"] = lang_code
        try:
            data = _request("/discover/movie", params=p, timeout=8)
            return data.get("results", []) or []
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=min(4, len(target_languages))) as executor:
        futures = [executor.submit(fetch_candidates_for_lang, c) for c in target_languages[:4]]
        for f in as_completed(futures):
            try:
                candidates.extend(f.result())
            except Exception:
                pass

    # If too few candidates, run a broader fallback
    if len(candidates) < 8:
        fallback_params = dict(discover_params)
        fallback_params.pop("with_genres", None)
        try:
            data = _request("/discover/movie", params=fallback_params, timeout=8)
            candidates.extend(data.get("results", []) or [])
        except Exception:
            pass

    # ============================================================
    # RULE-BASED MATCH SCORE CALCULATION
    # ============================================================
    # Maximum points total = 100.
    # 1. Genre match: up to 25 pts
    # 2. Mood match: up to 15 pts
    # 3. Language / Industry match: up to 20 pts
    # 4. Era / Year match: up to 15 pts
    # 5. Popularity / Acclaim match: up to 15 pts
    # 6. Quality baseline (vote_average): up to 10 pts
    # ============================================================

    scored_movies = []
    seen_ids = set()

    for item in candidates:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not mid or mid in seen_ids:
            continue
        seen_ids.add(mid)

        formatted = format_tmdb_movie(item)
        if not formatted.get("poster"):
            continue

        item_gids = item.get("genre_ids") or []
        item_lang = (item.get("original_language") or "").lower()
        item_year = formatted.get("year", "")
        item_votes = formatted.get("vote_count", 0)
        item_rating = formatted.get("rating", 0.0)
        item_pop = formatted.get("popularity", 0.0)

        score = 0.0
        match_breakdown = []

        # 1. Genre match (25 pts)
        genre_hits = len(set(item_gids).intersection(primary_genre_ids))
        if primary_genre_ids:
            if genre_hits > 0:
                pts = min(25.0, 18.0 + (genre_hits - 1) * 7.0)
                score += pts
                match_breakdown.append("Genre match")
            else:
                score += 5.0
        else:
            score += 20.0  # User didn't constrain genre

        # 2. Mood alignment (15 pts)
        mood_hits = len(set(item_gids).intersection(mood_genres))
        if mood_hits > 0:
            score += min(15.0, 10.0 + mood_hits * 3.0)
            match_breakdown.append("Mood fit")
        else:
            score += 5.0

        # 3. Language / Industry (20 pts)
        if lang_input in {"any", ""} and industry in {"any", "global"}:
            score += 20.0
        elif item_lang in target_languages:
            score += 20.0
            match_breakdown.append("Language/Region match")
        else:
            score += 5.0

        # 4. Era / Year (15 pts)
        if era == "any":
            score += 15.0
        elif item_year:
            try:
                y = int(item_year)
                if era == "2025_2026" and y >= 2025:
                    score += 15.0
                    match_breakdown.append("Year era fit")
                elif era == "2020_2024" and 2020 <= y <= 2024:
                    score += 15.0
                    match_breakdown.append("Year era fit")
                elif era == "2010s" and 2010 <= y <= 2019:
                    score += 15.0
                    match_breakdown.append("Year era fit")
                elif era == "classics" and y < 2000:
                    score += 15.0
                    match_breakdown.append("Year era fit")
                else:
                    score += 4.0
            except ValueError:
                score += 5.0
        else:
            score += 5.0

        # 5. Popularity preference (15 pts)
        if pop_pref == "blockbuster":
            if item_pop >= 30 or item_votes >= 1000:
                score += 15.0
                match_breakdown.append("Blockbuster appeal")
            else:
                score += 8.0
        elif pop_pref == "hidden_gem":
            if item_rating >= 7.2 and 40 <= item_votes <= 1200:
                score += 15.0
                match_breakdown.append("Hidden gem quality")
            else:
                score += 7.0
        else:  # acclaimed or any
            if item_rating >= 7.0 and item_votes >= 80:
                score += 15.0
                match_breakdown.append("High acclaim")
            else:
                score += 9.0

        # 6. Quality baseline (10 pts)
        score += min(10.0, (item_rating / 10.0) * 10.0)

        # Strictly cap match score at 100%
        final_percentage = min(100, max(25, round(score)))
        formatted["match_score"] = final_percentage
        formatted["match_reasons"] = match_breakdown

        scored_movies.append((final_percentage, formatted))

    # Sort deterministically by calculated match score descending
    scored_movies.sort(key=lambda x: (x[0], x[1].get("rating", 0), x[1].get("popularity", 0)), reverse=True)

    recommendations = [movie for _, movie in scored_movies[:8]]
    return recommendations


# ============================================================
# FEATURE 3: ANALYTICS DATA & DETERMINISTIC MOVIE OF THE MONTH
# ============================================================

_ANALYTICS_CACHE = {}
_ANALYTICS_CACHE_TTL = 900  # 15 minutes


def get_movie_of_the_month():
    """
    Select the Movie of the Month using a documented deterministic rule.
    
    DETERMINISTIC SELECTION RULE:
    1. Pool: Highly rated released 2026 titles with >= 40 TMDB votes.
    2. Scoring formula:
         Score = (vote_average * 10) + (log10(vote_count) * 8) + (min(popularity, 200) * 0.12)
    3. Winner: The highest-scoring title, deterministically evaluated for the current year-month.
    """
    today = datetime.now().date()
    current_year = today.year
    current_month = today.strftime("%B %Y")
    cache_key = f"motm_{today.year}_{today.month}"

    now = time.time()
    if cache_key in _ANALYTICS_CACHE:
        c_time, c_data = _ANALYTICS_CACHE[cache_key]
        if now - c_time < _ANALYTICS_CACHE_TTL:
            return c_data

    try:
        data = _request(
            "/discover/movie",
            params={
                "language": "en-US",
                "primary_release_date.gte": f"{current_year}-01-01",
                "primary_release_date.lte": today.isoformat(),
                "sort_by": "popularity.desc",
                "vote_count.gte": 40,
                "vote_average.gte": 6.8,
                "page": 1,
            },
            timeout=12,
        )
        candidates = data.get("results", []) or []
    except Exception as exc:
        print("MOTM discover error:", exc)
        candidates = []

    best_score = -1.0
    winner = None

    for item in candidates:
        if not isinstance(item, dict):
            continue
        v_avg = float(item.get("vote_average") or 0.0)
        v_count = int(item.get("vote_count") or 0)
        pop = float(item.get("popularity") or 0.0)

        if v_count < 20 or v_avg < 6.0:
            continue

        # Deterministic formula
        composite_score = (v_avg * 10.0) + (math.log10(v_count) * 8.0) + (min(pop, 200.0) * 0.12)
        if composite_score > best_score:
            best_score = composite_score
            winner = format_tmdb_movie(item)
            winner["motm_score"] = round(composite_score, 1)
            winner["motm_month"] = current_month

    if not winner and candidates:
        winner = format_tmdb_movie(candidates[0])
        winner["motm_month"] = current_month

    _ANALYTICS_CACHE[cache_key] = (now, winner)
    return winner


def get_analytics_movie_data():
    """
    Fetch comprehensive TMDB movie dataset for the Analytics Dashboard:
    - 2026 trending movies
    - 2026 most popular movies
    - highest-rated movies
    - most-voted movies
    - 2026 new releases
    - genre trends
    - language trends
    - industry/region trends
    - Movie of the Month
    """
    now = time.time()
    if "full_analytics" in _ANALYTICS_CACHE:
        c_time, c_data = _ANALYTICS_CACHE["full_analytics"]
        if now - c_time < _ANALYTICS_CACHE_TTL:
            return c_data

    today = datetime.now().date().isoformat()

    def fetch_endpoint(params):
        try:
            res = _request("/discover/movie", params=params, timeout=10)
            return [format_tmdb_movie(m) for m in res.get("results", [])[:10] if isinstance(m, dict)]
        except Exception as exc:
            print("Analytics section fetch error:", exc)
            return []

    jobs = {
        "trending_2026": {
            "language": "en-US",
            "primary_release_date.gte": "2026-01-01",
            "primary_release_date.lte": today,
            "sort_by": "popularity.desc",
            "vote_count.gte": 10,
        },
        "popular_2026": {
            "language": "en-US",
            "primary_release_date.gte": "2026-01-01",
            "primary_release_date.lte": today,
            "sort_by": "popularity.desc",
            "vote_count.gte": 20,
        },
        "highest_rated": {
            "language": "en-US",
            "sort_by": "vote_average.desc",
            "vote_count.gte": 1000,
        },
        "most_voted": {
            "language": "en-US",
            "sort_by": "vote_count.desc",
        },
        "new_releases_2026": {
            "language": "en-US",
            "primary_release_date.gte": "2026-01-01",
            "primary_release_date.lte": today,
            "sort_by": "primary_release_date.desc",
            "vote_count.gte": 5,
        },
    }

    results = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_map = {executor.submit(fetch_endpoint, p): k for k, p in jobs.items()}
        for f in as_completed(future_map):
            key = future_map[f]
            try:
                results[key] = f.result()
            except Exception:
                results[key] = []

    # Calculate genre trends, language trends, and industry trends from 2026 candidate sample
    sample_movies = results.get("trending_2026", []) + results.get("popular_2026", []) + results.get("new_releases_2026", [])

    genre_counter = {}
    lang_counter = {}
    industry_counter = {
        "Hollywood / Global": 0,
        "Bollywood (Hindi)": 0,
        "South Indian Cinema": 0,
        "Asian Cinema": 0,
        "European Cinema": 0,
    }

    for m in sample_movies:
        # Genre counts
        g_str = m.get("genres", "")
        for g in g_str.split(","):
            clean_g = g.strip()
            if clean_g:
                genre_counter[clean_g] = genre_counter.get(clean_g, 0) + 1

        # Language counts
        l_name = m.get("language", "Unknown")
        l_code = m.get("language_code", "")
        if l_name and l_name != "Unknown":
            lang_counter[l_name] = lang_counter.get(l_name, 0) + 1

        # Industry distribution
        if l_code == "hi":
            industry_counter["Bollywood (Hindi)"] += 1
        elif l_code in {"ta", "te", "ml", "kn"}:
            industry_counter["South Indian Cinema"] += 1
        elif l_code in {"ko", "ja", "zh"}:
            industry_counter["Asian Cinema"] += 1
        elif l_code in {"fr", "es", "de", "it"}:
            industry_counter["European Cinema"] += 1
        else:
            industry_counter["Hollywood / Global"] += 1

    genre_trends = sorted([{"name": k, "count": v} for k, v in genre_counter.items()], key=lambda x: x["count"], reverse=True)[:8]
    lang_trends = sorted([{"name": k, "count": v} for k, v in lang_counter.items()], key=lambda x: x["count"], reverse=True)[:8]
    industry_trends = sorted([{"name": k, "count": v} for k, v in industry_counter.items()], key=lambda x: x["count"], reverse=True)

    motm = get_movie_of_the_month()

    analytics_package = {
        "trending_2026": results.get("trending_2026", []),
        "popular_2026": results.get("popular_2026", []),
        "highest_rated": results.get("highest_rated", []),
        "most_voted": results.get("most_voted", []),
        "new_releases_2026": results.get("new_releases_2026", []),
        "genre_trends": genre_trends,
        "language_trends": lang_trends,
        "industry_trends": industry_trends,
        "movie_of_the_month": motm,
    }

    _ANALYTICS_CACHE["full_analytics"] = (now, analytics_package)
    return analytics_package
