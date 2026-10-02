# ============================================================
# WATCHKARO — REAL USER ANALYTICS TRACKER
# ============================================================
#
# Tracks real user events:
# - total searches
# - most searched movies
# - recommendations generated
# - most recommended movies
# - quiz completions
# - popular user-selected genres
# - popular user-selected languages
# - popular user-selected industries
#
# RULE: Only display metrics if the application actually records them.
# NEVER invent statistics.
# ============================================================

import json
import os
import threading
from collections import Counter

ANALYTICS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_analytics.json")
_lock = threading.Lock()

_data = {
    "total_searches": 0,
    "searched_movies": {},
    "recommendations_generated": 0,
    "recommended_movies": {},
    "quiz_completions": 0,
    "quiz_genres": {},
    "quiz_languages": {},
    "quiz_industries": {},
}

def _load_data():
    global _data
    if os.path.exists(ANALYTICS_FILE):
        try:
            with open(ANALYTICS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    for k in _data:
                        if k in saved:
                            _data[k] = saved[k]
        except Exception as exc:
            print("Analytics load error:", exc)

def _save_data():
    try:
        with open(ANALYTICS_FILE, "w", encoding="utf-8") as f:
            json.dump(_data, f, indent=2)
    except Exception as exc:
        print("Analytics save error:", exc)

# Initial load
_load_data()


def record_search(query):
    """Record a user search query."""
    clean_q = str(query or "").strip()
    if not clean_q:
        return
    with _lock:
        _data["total_searches"] += 1
        key = clean_q.title()
        _data["searched_movies"][key] = _data["searched_movies"].get(key, 0) + 1
        _save_data()


def record_recommendation(seed_title, recommended_titles):
    """Record a recommendation generation event and the recommended movies."""
    with _lock:
        _data["recommendations_generated"] += 1
        for title in recommended_titles:
            clean = str(title or "").strip()
            if clean:
                _data["recommended_movies"][clean] = _data["recommended_movies"].get(clean, 0) + 1
        _save_data()


def record_quiz_completion(genre_name="", language_name="", industry_name=""):
    """Record a quiz completion event with user-selected preferences."""
    with _lock:
        _data["quiz_completions"] += 1
        if genre_name:
            _data["quiz_genres"][genre_name] = _data["quiz_genres"].get(genre_name, 0) + 1
        if language_name:
            _data["quiz_languages"][language_name] = _data["quiz_languages"].get(language_name, 0) + 1
        if industry_name:
            _data["quiz_industries"][industry_name] = _data["quiz_industries"].get(industry_name, 0) + 1
        _save_data()


def get_user_analytics():
    """Return real recorded metrics without inventing any statistics."""
    with _lock:
        searched = sorted(
            [{"movie": k, "count": v} for k, v in _data["searched_movies"].items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:8]

        recommended = sorted(
            [{"movie": k, "count": v} for k, v in _data["recommended_movies"].items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:8]

        genres = sorted(
            [{"name": k, "count": v} for k, v in _data["quiz_genres"].items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:6]

        languages = sorted(
            [{"name": k, "count": v} for k, v in _data["quiz_languages"].items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:6]

        industries = sorted(
            [{"name": k, "count": v} for k, v in _data["quiz_industries"].items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:6]

        return {
            "total_searches": _data["total_searches"],
            "most_searched_movies": searched,
            "recommendations_generated": _data["recommendations_generated"],
            "most_recommended_movies": recommended,
            "quiz_completions": _data["quiz_completions"],
            "popular_genres": genres,
            "popular_languages": languages,
            "popular_industries": industries,
            "has_data": (
                _data["total_searches"] > 0
                or _data["recommendations_generated"] > 0
                or _data["quiz_completions"] > 0
            ),
        }
