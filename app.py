import os
from functools import lru_cache

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from tmdb_api import (
    search_movies as tmdb_search_movies,
    get_movie_recommendations,
    get_movie_details,
    get_poster_url,
)

load_dotenv()

app = Flask(__name__)

SEARCH_LIMIT = 10


def safe_string(value, default=""):
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def get_language_name(code):
    names = {
        "en": "English",
        "hi": "Hindi",
        "ta": "Tamil",
        "te": "Telugu",
        "ml": "Malayalam",
        "kn": "Kannada",
        "bn": "Bengali",
        "mr": "Marathi",
        "gu": "Gujarati",
        "ja": "Japanese",
        "ko": "Korean",
        "zh": "Chinese",
        "es": "Spanish",
        "fr": "French",
    }
    code = safe_string(code).lower().strip()
    return names.get(code, code.upper() if code else "Unknown")


def tmdb_movie_to_dict(movie):
    if not isinstance(movie, dict):
        return {}

    title = safe_string(
        movie.get("title") or movie.get("name")
    )

    release_date = safe_string(
        movie.get("release_date") or movie.get("first_air_date")
    )

    year = release_date[:4] if release_date else "N/A"

    poster_path = safe_string(
        movie.get("poster_path")
    )

    poster = ""
    if poster_path:
        poster = get_poster_url(poster_path)

    genre_names = movie.get("genres") or []

    if genre_names and isinstance(genre_names[0], dict):
        genres = ", ".join(
            safe_string(g.get("name"))
            for g in genre_names
            if g.get("name")
        )
    else:
        genres = ", ".join(
            safe_string(g)
            for g in genre_names
            if g
        )

    return {
        "title": title,
        "year": year,
        "release_date": release_date,
        "language": get_language_name(
            movie.get("original_language", "")
        ),
        "language_code": safe_string(
            movie.get("original_language", "")
        ),
        "genres": genres,
        "rating": round(
            float(movie.get("vote_average") or 0),
            1,
        ),
        "votes": int(
            movie.get("vote_count") or 0
        ),
        "popularity": round(
            float(movie.get("popularity") or 0),
            2,
        ),
        "overview": safe_string(
            movie.get("overview")
        ),
        "poster": poster,
        "poster_path": poster_path,
        "tmdb_id": movie.get("id", ""),
        "media_type": movie.get(
            "media_type",
            "movie",
        ),
        "source": "TMDB",
        "match_score": 0,
        "cast": "",
        "director": "",
    }


@lru_cache(maxsize=256)
def cached_tmdb_search(query):
    try:
        results = tmdb_search_movies(
            query,
            page=1,
        ) or []
        return tuple(results[:20])
    except Exception as exc:
        print("TMDB search error:", repr(exc))
        return tuple()


@lru_cache(maxsize=256)
def cached_tmdb_recommendations(tmdb_id):
    try:
        results = get_movie_recommendations(
            int(tmdb_id),
            page=1,
        ) or []
        return tuple(results[:20])
    except Exception as exc:
        print(
            "TMDB recommendation error:",
            repr(exc),
        )
        return tuple()


def enrich_selected_movie(movie):
    if not movie:
        return movie

    tmdb_id = movie.get("tmdb_id")

    if not tmdb_id:
        return movie

    try:
        details = get_movie_details(
            int(tmdb_id)
        ) or {}

        enriched = tmdb_movie_to_dict(
            details
        )

        if not enriched.get("title"):
            enriched["title"] = movie.get(
                "title",
                "",
            )

        credits = details.get(
            "credits",
            {},
        )

        cast = credits.get(
            "cast",
            [],
        ) or []

        crew = credits.get(
            "crew",
            [],
        ) or []

        cast_names = [
            safe_string(person.get("name"))
            for person in cast[:5]
            if person.get("name")
        ]

        director_names = [
            safe_string(person.get("name"))
            for person in crew
            if person.get("job") == "Director"
        ][:2]

        enriched["cast"] = ", ".join(
            cast_names
        )

        enriched["director"] = ", ".join(
            director_names
        )

        enriched["source"] = "TMDB"
        enriched["media_type"] = "movie"

        return enriched

    except Exception as exc:
        print(
            "Movie detail error:",
            repr(exc),
        )
        return movie


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
        home_sections={},
    )


@app.route("/search")
def search():
    query = request.args.get(
        "q",
        "",
    ).strip()

    if len(query) < 2:
        return jsonify([])

    results = cached_tmdb_search(
        query
    )

    output = []

    for movie in results:
        item = tmdb_movie_to_dict(
            movie
        )

        if item.get("title"):
            output.append(item)

    return jsonify(
        output[:SEARCH_LIMIT]
    )


@app.route(
    "/recommend",
    methods=["GET", "POST"],
)
def recommendation():

    title = (
        request.args.get("movie")
        or request.form.get("movie")
        or ""
    ).strip()

    if not title:
        return render_template(
            "index.html",
            selected_movie=None,
            recommendations=[],
            search_results=[],
            selected_media_type=None,
            error="Please enter a movie title.",
            notice=None,
            home_sections={},
        )

    results = cached_tmdb_search(
        title
    )

    if not results:
        return render_template(
            "index.html",
            selected_movie=None,
            recommendations=[],
            search_results=[],
            selected_media_type=None,
            error=f"No movie found for '{title}'.",
            notice=None,
            home_sections={},
        )

    selected = None

    normalized_title = title.lower().strip()

    for movie in results:
        movie_title = safe_string(
            movie.get("title")
            or movie.get("name")
        ).lower().strip()

        if movie_title == normalized_title:
            selected = movie
            break

    if selected is None:
        selected = results[0]

    selected_movie = tmdb_movie_to_dict(
        selected
    )

    if selected.get("media_type") == "tv":
        return render_template(
            "index.html",
            selected_movie=selected_movie,
            recommendations=[],
            search_results=[],
            selected_media_type="tv",
            error=None,
            notice=(
                "This is a TV series. "
                "Movie recommendations are "
                "available for movies."
            ),
            home_sections={},
        )

    selected_movie = enrich_selected_movie(
        selected_movie
    )

    tmdb_id = selected_movie.get(
        "tmdb_id"
    )

    raw_recommendations = (
        cached_tmdb_recommendations(
            str(tmdb_id)
        )
        if tmdb_id
        else tuple()
    )

    recommendations = [
        tmdb_movie_to_dict(movie)
        for movie in raw_recommendations
    ]

    for index, movie in enumerate(
        recommendations
    ):
        movie["match_score"] = max(
            60,
            95 - index * 2,
        )

    return render_template(
        "index.html",
        selected_movie=selected_movie,
        recommendations=recommendations,
        search_results=[],
        selected_media_type="movie",
        error=None,
        notice=None,
        home_sections={},
    )


@app.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "WatchKaro",
        }
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                5000,
            )
        ),
        debug=False,
    )
