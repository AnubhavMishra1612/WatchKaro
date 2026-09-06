# WatchKaro 🎬

### Intelligent Movie Recommendation System Using Hybrid Content-Based Filtering

**Tagline:** Search a movie. Find your next watch.

WatchKaro is a web-based movie recommendation system that recommends relevant movies using a hybrid content-based recommendation approach combined with TMDB data.

## Features

- Movie search
- Hybrid movie recommendation
- Story and metadata-based similarity
- Genre and keyword matching
- Actor and director-aware recommendations
- TMDB integration
- Movie posters and details
- Match percentage for recommendations
- Responsive web interface

## Technology Stack

- Python
- Flask
- Pandas
- NumPy
- Scikit-learn
- BM25
- TMDB API
- HTML
- CSS
- JavaScript

## Project Structure

```text
WatchKaro/
├── app.py
├── final_recommender.py
├── tmdb_api.py
├── templates/
│   ├── index.html
│   └── movie_card.html
├── static/
│   ├── style.css
│   └── script.js
├── .env.example
└── .gitignore

Setup
Clone the repository.
Create a Python virtual environment.
Install the required dependencies.
Add TMDB API credentials to .env.
Run the Flask application.
Note

The trained recommendation model and large dataset are not included in this repository because of their file size.

Author

Anubhav Mishra
