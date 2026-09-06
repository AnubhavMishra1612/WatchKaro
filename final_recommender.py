import os
import re
import math
import numpy as np
import pandas as pd
from difflib import SequenceMatcher
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DATA_PATH = r"C:\movie-recommender\data\clean_movies.csv"

SEARCH_ALIASES = {
    "bahubali": "baahubali",
    "babubali": "baahubali",
    "spiderman": "spider-man",
    "spidar man": "spider-man",
    "interstelar": "interstellar",
    "3idiots": "3 idiots"
}

THEME_DICTIONARY = {
    "mythology": ["mythology", "myth", "god", "divine", "devotion", "hindu", "ramayana", "mahabharata", "hanuman", "krishna", "shiva", "blessing"],
    "epic_fantasy": ["kingdom", "warrior", "empire", "dynasty", "sword", "royal", "epic", "throne", "ruler", "battle"],
    "education": ["college", "engineering", "student", "school", "friendship", "university", "teacher", "degree", "coming-of-age"],
    "space_scifi": ["space", "astronaut", "planet", "wormhole", "time", "gravity", "galaxy", "universe", "relativity", "black hole"],
    "superhero": ["superhero", "vigilante", "villain", "mutant", "powers", "comic book", "mask", "save the world"]
}

df = None
tfidf_matrix = None
vectorizer = None

def normalize_text(text):
    if not text or pd.isna(text):
        return ""
    text = str(text).lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def make_match_key(text):
    text = normalize_text(text)
    return re.sub(r"([aeiou])\1+", r"\1", text)

def safe_float(val, default=0.0):
    try:
        if pd.isna(val) or val is None:
            return default
        return float(val)
    except:
        return default

def safe_int(val, default=0):
    try:
        if pd.isna(val) or val is None:
            return default
        return int(float(val))
    except:
        return default

def initialize_engine():
    global df, tfidf_matrix, vectorizer
    if df is not None:
        return
        
    print("[Recommender Initialization] Loading CSV dataset...")
    df = pd.read_csv(DATA_PATH)
    
    df["clean_title"] = df["title"].apply(normalize_text)
    df["match_key"] = df["clean_title"].apply(make_match_key)
    
    df["overview_clean"] = df["overview"].fillna("").astype(str)
    df["genres_clean"] = df["genres"].fillna("").astype(str)
    df["keywords_clean"] = df["keywords"].fillna("").astype(str) if "keywords" in df.columns else ""
    df["tagline_clean"] = df["tagline"].fillna("").astype(str) if "tagline" in df.columns else ""
    df["lang_clean"] = df["original_language"].fillna("en").astype(str).str.lower()
    
    df["soup"] = (
        df["clean_title"] + " " +
        df["genres_clean"] + " " +
        df["keywords_clean"] + " " +
        df["tagline_clean"] + " " +
        df["overview_clean"]
    )
    
    print("[Recommender Initialization] Building TF-IDF matrix...")
    vectorizer = TfidfVectorizer(max_features=25000, stop_words="english", ngram_range=(1, 2))
    tfidf_matrix = vectorizer.fit_transform(df["soup"])
    print(f"[Recommender Initialization] Matrix built successfully: {tfidf_matrix.shape}")

initialize_engine()

def resolve_entity(query, candidates_list):
    """
    Principled search and entity resolution score.
    """
    query_clean = normalize_text(query)
    query_clean = SEARCH_ALIASES.get(query_clean, query_clean)
    query_key = make_match_key(query_clean)
    
    scored = []
    for cand in candidates_list:
        cand_title = normalize_text(cand.get("title", ""))
        cand_key = make_match_key(cand_title)
        
        # Title match scoring
        if cand_title == query_clean:
            title_score = 100.0
        elif cand_key == query_key:
            title_score = 90.0
        elif cand_title.startswith(query_clean):
            title_score = 75.0
        elif query_clean in cand_title:
            title_score = 60.0
        else:
            ratio = SequenceMatcher(None, query_clean, cand_title).ratio()
            title_score = ratio * 50.0 if ratio >= 0.6 else 0.0
            
        votes = safe_float(cand.get("vote_count", 0))
        popularity = safe_float(cand.get("popularity", 0))
        
        vote_score = math.log1p(votes) * 2.5
        pop_score = math.log1p(popularity) * 1.5
        
        media_penalty = 0.0 if cand.get("media_type", "movie") == "movie" else -20.0
        
        total_score = title_score + vote_score + pop_score + media_penalty
        
        cand_copy = dict(cand)
        cand_copy["resolution_score"] = round(total_score, 2)
        scored.append((total_score, cand_copy))
        
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else None

def extract_themes(text):
    text_clean = normalize_text(text)
    matched_themes = set()
    for theme, keywords in THEME_DICTIONARY.items():
        if any(kw in text_clean for kw in keywords):
            matched_themes.add(theme)
    return matched_themes

def compute_multi_stage_recommendations(seed_idx, seed_dict=None, top_n=20):
    global df, tfidf_matrix
    
    if seed_idx is not None:
        seed_row = df.iloc[seed_idx]
        seed_title = seed_row["title"]
        seed_lang = seed_row["lang_clean"]
        seed_genres = set(normalize_text(seed_row["genres_clean"]).split())
        seed_soup = seed_row["soup"]
        seed_vec = tfidf_matrix[seed_idx]
    else:
        seed_title = seed_dict.get("title", "")
        seed_lang = normalize_text(seed_dict.get("original_language", "en"))
        seed_genres = set(normalize_text(seed_dict.get("genres", "")).split())
        seed_soup = seed_title + " " + normalize_text(seed_dict.get("overview", ""))
        seed_vec = vectorizer.transform([seed_soup])
        
    seed_themes = extract_themes(seed_soup)
    
    # Stage 1: Candidate Generation (Retrieval of Top 500 by TF-IDF Cosine Similarity)
    cosine_sims = cosine_similarity(seed_vec, tfidf_matrix).flatten()
    candidate_indices = np.argpartition(cosine_sims, -500)[-500:]
    
    ranked_candidates = []
    
    C = df["vote_average"].mean()
    m = 50  # Bayesian min votes cutoff
    
    for idx in candidate_indices:
        if seed_idx is not None and idx == seed_idx:
            continue
            
        row = df.iloc[idx]
        cand_title = row["title"]
        
        if normalize_text(cand_title) == normalize_text(seed_title):
            continue
            
        content_score = float(cosine_sims[idx])
        
        # Genre Score (Jaccard)
        cand_genres = set(normalize_text(row["genres_clean"]).split())
        genre_score = len(seed_genres.intersection(cand_genres)) / max(1, len(seed_genres.union(cand_genres)))
        
        # Theme Score
        cand_themes = extract_themes(row["soup"])
        theme_score = len(seed_themes.intersection(cand_themes)) / max(1, len(seed_themes)) if seed_themes else 0.0
        
        # Language Score
        cand_lang = row["lang_clean"]
        if cand_lang == seed_lang:
            lang_score = 1.0
        elif seed_lang in ["hi", "te", "ta", "ml", "kn"] and cand_lang in ["hi", "te", "ta", "ml", "kn"]:
            lang_score = 0.6
        else:
            lang_score = 0.2
            
        # Quality & Popularity
        v = safe_float(row["vote_count"])
        R = safe_float(row["vote_average"])
        bayesian_rating = (v / (v + m)) * R + (m / (v + m)) * C
        quality_score = bayesian_rating / 10.0
        
        pop = safe_float(row["popularity"])
        popularity_score = min(1.0, math.log1p(pop) / 5.0)
        
        final_score = (
            0.35 * content_score +
            0.25 * genre_score +
            0.15 * theme_score +
            0.15 * lang_score +
            0.05 * quality_score +
            0.05 * popularity_score
        )
        
        item = {
            "title": cand_title,
            "year": str(row.get("release_date", ""))[:4],
            "language": cand_lang.upper(),
            "genres": row["genres_clean"],
            "rating": round(R, 1),
            "votes": int(v),
            "popularity": round(pop, 2),
            "overview": row["overview_clean"],
            "poster": row.get("poster_path", ""),
            "tmdb_id": row.get("tmdb_id", ""),
            "source": "LOCAL",
            "scores": {
                "content_score": round(content_score, 3),
                "genre_score": round(genre_score, 3),
                "theme_score": round(theme_score, 3),
                "language_score": round(lang_score, 3),
                "quality_score": round(quality_score, 3),
                "popularity_score": round(popularity_score, 3),
                "final_score": round(final_score, 3)
            }
        }
        ranked_candidates.append((final_score, item))
        
    ranked_candidates.sort(key=lambda x: x[0], reverse=True)
    
    # Terminal Diagnostic Logging
    print(f"\n================ RECOMMENDATION DIAGNOSTICS FOR: '{seed_title}' ================")
    print(f"Seed Language: {seed_lang} | Detected Themes: {list(seed_themes)}")
    print(f"{'Title':<35} | {'Final':<6} | {'Cont':<5} | {'Genr':<5} | {'Them':<5} | {'Lang':<5}")
    print("-" * 75)
    
    final_results = []
    for score, item in ranked_candidates[:top_n]:
        sc = item["scores"]
        print(f"{item['title'][:34]:<35} | {sc['final_score']:<6} | {sc['content_score']:<5} | {sc['genre_score']:<5} | {sc['theme_score']:<5} | {sc['language_score']:<5}")
        final_results.append(item)
    print("================================================================================\n")
    
    return final_results

def recommend(title, top_n=20):
    global df
    query_clean = normalize_text(title)
    query_clean = SEARCH_ALIASES.get(query_clean, query_clean)
    
    exact_matches = df[df["clean_title"] == query_clean]
    if not exact_matches.empty:
        idx = exact_matches.index[0]
        return compute_multi_stage_recommendations(idx, top_n=top_n)
        
    key_matches = df[df["match_key"] == make_match_key(query_clean)]
    if not key_matches.empty:
        idx = key_matches.index[0]
        return compute_multi_stage_recommendations(idx, top_n=top_n)
        
    return []