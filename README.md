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
- TMDB API integration
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

## Recommendation Approach

WatchKaro uses a hybrid content-based recommendation system that combines multiple signals such as:

- Story similarity
- Genre similarity
- Keyword similarity
- Metadata similarity
- Theme similarity
- Language preference
- Movie quality and popularity
- Actor and director relationships

The system uses local movie data together with TMDB data to improve recommendation quality.

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
├── .gitignore
└── README.md
```

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/AnubhavMishra1612/WatchKaro.git
cd WatchKaro
```

### 2. Create Virtual Environment

```bash
python -m venv venv
```

### 3. Activate Virtual Environment

**Windows:**

```bash
venv\Scripts\activate
```

### 4. Install Dependencies

```bash
pip install flask pandas numpy scikit-learn rank-bm25 requests python-dotenv
```

### 5. Configure TMDB API

Create a `.env` file in the project root:

```text
TMDB_API_KEY=your_api_key
TMDB_ACCESS_TOKEN=your_access_token
```

Do not upload your real `.env` file to GitHub.

### 6. Add Dataset and Trained Model

The large dataset and trained model are not included in this repository because of their file size.

Place the cleaned dataset at:

```text
data/clean_movies.csv
```

Place the trained recommendation model at:

```text
models/movie_recommender_v4.pkl
```

### 7. Run the Application

```bash
python app.py
```

Open the application in your browser:

```text
http://127.0.0.1:5000
```

## Security

API credentials are stored in environment variables and are not included in the GitHub repository.

## Limitations

- The current system is primarily content-based.
- It does not use user login, watch history, or collaborative filtering.
- Recommendation quality depends on available movie metadata and TMDB information.
- The trained model and large dataset are stored separately because of their size.

## Future Scope

- User accounts and profiles
- Personalized recommendations
- Watch history
- Ratings and feedback
- Collaborative filtering
- Deep learning-based ranking
- Improved recommendation personalization

## Author

**Anubhav Mishra**
