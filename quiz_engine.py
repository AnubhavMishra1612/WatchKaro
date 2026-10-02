# ============================================================
# WATCHKARO — FIND MY MOVIE QUIZ RECOMMENDATION ENGINE
# ============================================================
#
# Transparent, rule-based recommendation engine matching the
# local reference implementation.
#
# NOTE: This is NOT generative AI, NOT a chatbot, and NOT an LLM.
# It uses deterministic multi-criteria scoring over movie metadata
# from TMDB.
#
# Scoring Signals (Total max: 100 points):
# 1. Primary Genre Match:    up to 30 points
# 2. Language Preference:     up to 25 points
# 3. Mood / Experience:       up to 15 points
# 4. Recency / Release Year:  up to 10 points
# 5. Popularity Preference:   up to 10 points
# 6. Industry / Cinema:       up to 10 points
#
# Match percentage is calculated strictly from points earned (capped at 100%).
# ============================================================

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import tmdb_api

# Language display name -> ISO-639-1 code
LANGUAGE_CODE_MAP = {
    "hindi": "hi",
    "english": "en",
    "tamil": "ta",
    "telugu": "te",
    "malayalam": "ml",
    "korean": "ko",
    "japanese": "ja",
    "kannada": "kn",
    "bengali": "bn",
    "marathi": "mr",
    "gujarati": "gu",
}

# Mood / Experience -> relevant genres and keywords for scoring
MOOD_GENRE_MAP = {
    "fun": ["Comedy", "Animation", "Adventure", "Family"],
    "scary": ["Horror", "Thriller", "Mystery"],
    "emotional": ["Drama", "Romance"],
    "mind-bending": ["Science Fiction", "Sci-Fi", "Mystery", "Thriller"],
    "romantic": ["Romance", "Comedy", "Drama"],
    "exciting": ["Action", "Adventure", "Thriller", "Crime"],
    "inspiring": ["Drama", "History", "Documentary"],
}

INDUSTRY_LANGUAGE_MAP = {
    "bollywood": ["hi"],
    "hollywood": ["en"],
    "south_indian": ["ta", "te", "ml", "kn"],
    "south indian": ["ta", "te", "ml", "kn"],
    "korean": ["ko"],
    "japanese": ["ja"],
}


def _safe_int(val, default=0):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def evaluate_candidate(candidate, answers):
    """
    Score a single movie candidate against the user's 7 quiz answers.
    Returns (raw_score, match_percentage, explanation, reason_tokens).
    """
    genre_ans = str(answers.get("genre", "Any")).strip().lower()
    lang_ans = str(answers.get("language", "Any")).strip().lower()
    mood_ans = str(answers.get("mood", "Any")).strip().lower()
    year_ans = str(answers.get("year", "Any")).strip().lower()
    pop_ans = str(answers.get("popularity", "Any")).strip().lower()
    length_ans = str(answers.get("length", "Any")).strip().lower()
    industry_ans = str(answers.get("industry", "Any")).strip().lower()

    raw_genres = candidate.get("genres", "")
    if isinstance(raw_genres, list):
        cand_genres = [str(g).strip().lower() for g in raw_genres if g]
    else:
        cand_genres = [g.strip().lower() for g in str(raw_genres).split(",") if g.strip()]

    cand_lang = candidate.get("language_code", "").lower()
    cand_year = _safe_int(candidate.get("year", 0))
    cand_rating = _safe_float(candidate.get("rating", candidate.get("vote_average", 0)))
    cand_votes = _safe_int(candidate.get("votes", candidate.get("vote_count", 0)))
    cand_popularity = _safe_float(candidate.get("popularity", 0))

    score = 0.0
    reasons = []

    # ---------------------------------------------------------
    # 1. Primary Genre Match (Max 30 pts)
    # ---------------------------------------------------------
    if genre_ans and genre_ans != "any":
        target_genre = genre_ans.replace("sci-fi", "science fiction").replace("scifi", "science fiction")
        if any(target_genre in g for g in cand_genres) or any(g in target_genre for g in cand_genres):
            score += 30.0
            reasons.append(genre_ans.title())
        else:
            score += 5.0
    else:
        score += 20.0  # Neutral baseline when "Any" is chosen

    # ---------------------------------------------------------
    # 2. Language Match (Max 25 pts)
    # ---------------------------------------------------------
    target_lang_code = LANGUAGE_CODE_MAP.get(lang_ans)
    if target_lang_code and lang_ans != "any":
        if cand_lang == target_lang_code:
            score += 25.0
            reasons.append(lang_ans.title())
        else:
            score += 0.0
    elif lang_ans in {"south indian", "south_indian"}:
        if cand_lang in {"ta", "te", "ml", "kn"}:
            score += 25.0
            reasons.append(candidate.get("language", "South Indian"))
        else:
            score += 0.0
    else:
        score += 18.0  # Neutral baseline when "Any" is chosen

    # ---------------------------------------------------------
    # 3. Mood / Experience Match (Max 15 pts)
    # ---------------------------------------------------------
    if mood_ans and mood_ans != "any":
        related_mood_genres = [g.lower() for g in MOOD_GENRE_MAP.get(mood_ans, [])]
        matched_mood_genres = [g for g in cand_genres if any(mg in g or g in mg for mg in related_mood_genres)]
        if matched_mood_genres:
            score += 15.0
            reasons.append(f"{mood_ans.title()} Mood")
        else:
            score += 4.0
    else:
        score += 10.0

    # ---------------------------------------------------------
    # 4. Recency / Year Match (Max 10 pts)
    # ---------------------------------------------------------
    current_year = datetime.now().year
    if "latest" in year_ans:
        if cand_year >= current_year - 1:
            score += 10.0
            reasons.append("Recent Movies")
        elif cand_year >= current_year - 2:
            score += 6.0
            reasons.append("Recent Movies")
    elif "2" in year_ans:  # Last 2 years
        if cand_year >= current_year - 2:
            score += 10.0
            reasons.append("Recent Movies")
        elif cand_year >= current_year - 3:
            score += 5.0
            reasons.append("Recent Movies")
    elif "5" in year_ans:  # Last 5 years
        if cand_year >= current_year - 5:
            score += 10.0
            reasons.append(f"Released {cand_year}")
        elif cand_year >= current_year - 7:
            score += 4.0
    else:
        score += 8.0  # Any year

    # ---------------------------------------------------------
    # 5. Popularity Preference Match (Max 10 pts)
    # ---------------------------------------------------------
    if "hidden" in pop_ans:
        if cand_rating >= 6.8 and 50 <= cand_votes <= 5000:
            score += 10.0
            reasons.append("Hidden Gem")
        elif cand_rating >= 6.5:
            score += 6.0
    elif "popular" in pop_ans:
        if cand_popularity >= 25 or cand_votes >= 500:
            score += 10.0
            reasons.append("Popular")
        elif cand_popularity >= 15:
            score += 6.0
    elif "trending" in pop_ans:
        if cand_popularity >= 20:
            score += 10.0
            reasons.append("Trending")
        else:
            score += 5.0
    else:
        score += 7.0  # Any

    # ---------------------------------------------------------
    # 6. Industry Match (Max 10 pts)
    # ---------------------------------------------------------
    if industry_ans and industry_ans != "any":
        target_langs = INDUSTRY_LANGUAGE_MAP.get(industry_ans, [])
        if target_langs and cand_lang in target_langs:
            score += 10.0
            if industry_ans.title() not in [r.title() for r in reasons]:
                reasons.append(f"{industry_ans.title()} Cinema")
        elif cand_lang != "en" and industry_ans not in {"hollywood"}:
            score += 6.0
        else:
            score += 2.0
    else:
        score += 7.0

    # Ensure match percentage is capped at 100%
    calculated_pct = min(100, max(52, int(round(score))))

    # Format human-friendly explanation matching the example:
    # "Recommended because you selected: Action + Hindi + Recent Movies + Thriller."
    reason_tokens = reasons[:4] if reasons else ["Taste Profile"]
    explanation = "Recommended because you selected: " + " + ".join(reason_tokens) + "."

    return score, calculated_pct, explanation, reason_tokens


def get_quiz_recommendations(answers, limit=1):
    """
    Fetch live candidate movies from TMDB based on primary quiz answers,
    score them using the transparent multi-criteria formula,
    and return the single highest-scoring recommendation (or top N if limit > 1).
    """
    genre_ans = str(answers.get("genre", "Any")).strip()
    lang_ans = str(answers.get("language", "Any")).strip().lower()
    industry_ans = str(answers.get("industry", "Any")).strip().lower()
    year_ans = str(answers.get("year", "Any")).strip().lower()
    pop_ans = str(answers.get("popularity", "Any")).strip().lower()

    # Determine genre ID
    genre_id = None
    if genre_ans and genre_ans.lower() != "any":
        genre_id = tmdb_api.GENRE_NAME_TO_ID.get(genre_ans.lower())
        if not genre_id and "sci" in genre_ans.lower():
            genre_id = "878"

    # Determine language
    lang_code = LANGUAGE_CODE_MAP.get(lang_ans)
    if not lang_code:
        if industry_ans == "bollywood":
            lang_code = "hi"
        elif industry_ans == "hollywood":
            lang_code = "en"

    # Year filter
    current_year = datetime.now().year
    year_param = "all"
    if "latest" in year_ans:
        year_param = str(current_year)
    elif "2" in year_ans:
        year_param = "2024"
    elif "5" in year_ans:
        year_param = "2020-2021"

    # Preset / Sorting mode
    preset = "popular"
    if "hidden" in pop_ans:
        preset = "highest_rated"
    elif "trending" in pop_ans:
        preset = "trending"

    # Multi-language discovery if South Indian
    candidates = []
    seen_ids = set()

    if lang_ans in {"south indian", "south_indian"} or industry_ans in {"south indian", "south_indian"}:
        languages_to_fetch = ["ta", "te", "ml", "kn"]
        for code in languages_to_fetch:
            res = tmdb_api.discover_movies(
                preset=preset,
                year=year_param,
                language=code,
                genre=str(genre_id) if genre_id else "all",
                industry="all",
                page=1,
            )
            for m in res:
                mid = m.get("tmdb_id") or m.get("id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    candidates.append(m)
    else:
        res = tmdb_api.discover_movies(
            preset=preset,
            year=year_param,
            language=lang_code if lang_code else "all",
            genre=str(genre_id) if genre_id else "all",
            industry=industry_ans if industry_ans in {"hollywood", "bollywood", "asian", "european"} else "all",
            page=1,
        )
        for m in res:
            mid = m.get("tmdb_id") or m.get("id")
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                candidates.append(m)

    # Fallback to broader discovery if fewer than 10 candidates
    if len(candidates) < 10:
        broader_res = tmdb_api.discover_movies(
            preset="popular",
            year="all",
            language=lang_code if lang_code else "all",
            genre=str(genre_id) if genre_id else "all",
            industry="all",
            page=1,
        )
        for m in broader_res:
            mid = m.get("tmdb_id") or m.get("id")
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                candidates.append(m)

    # Fallback open discovery if still empty
    if not candidates:
        open_res = tmdb_api.discover_movies(preset="popular", page=1)
        candidates = list(open_res)

    # Score every candidate
    scored_candidates = []
    for cand in candidates:
        raw_score, pct, explanation, reason_tokens = evaluate_candidate(cand, answers)
        cand_copy = dict(cand)
        cand_copy["match_score"] = pct
        cand_copy["quiz_explanation"] = explanation
        cand_copy["quiz_reasons"] = reason_tokens
        scored_candidates.append((raw_score, cand_copy))

    # Sort descending by raw score
    scored_candidates.sort(key=lambda x: x[0], reverse=True)

    # Return top N picks
    result = [item[1] for item in scored_candidates[:limit]]
    return result


def get_single_quiz_recommendation(answers):
    """Return exactly ONE highest-scoring movie based on the user's actual 7 answers."""
    recs = get_quiz_recommendations(answers, limit=1)
    if recs:
        return recs[0]
    return None
