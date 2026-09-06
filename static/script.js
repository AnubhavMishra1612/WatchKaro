(() => {
    "use strict";

    // =========================================================
    // WATCHKARO — MOVIE SEARCH JAVASCRIPT
    //
    // Features:
    // 1. Local + TMDB search through Flask /search endpoint
    // 2. Debounced search
    // 3. Cancels old/stale requests
    // 4. Autocomplete dropdown
    // 5. Click a movie -> automatically open recommendations
    // 6. Keyboard navigation
    // 7. Enter key support
    // 8. Escape key support
    // 9. Safe HTML escaping
    // 10. Prevents old movie metadata from being reused
    // =========================================================


    // =========================================================
    // 1. GET HTML ELEMENTS
    // =========================================================

    const movieInput =
        document.getElementById("movieInput");

    const suggestions =
        document.getElementById("suggestions");

    const searchForm =
        document.getElementById("searchForm") ||
        document.querySelector(".search-box");

    const selectedMediaType =
        document.getElementById("selectedMediaType");

    const selectedSource =
        document.getElementById("selectedSource");

    const selectedTmdbId =
        document.getElementById("selectedTmdbId");

    const recommendButton =
        document.getElementById("recommendButton");


    // =========================================================
    // 2. CHECK REQUIRED ELEMENTS
    // =========================================================

    if (
        !movieInput ||
        !suggestions ||
        !searchForm
    ) {

        console.error(
            "WatchKaro: Search elements not found."
        );

        return;
    }


    // =========================================================
    // 3. SEARCH STATE
    // =========================================================

    let searchTimer = null;

    let currentController = null;

    let requestCounter = 0;

    let activeIndex = -1;

    let currentResults = [];

    let isSubmitting = false;


    // =========================================================
    // 4. SAFE HTML ESCAPE
    // =========================================================

    function escapeHtml(value) {

        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    // =========================================================
    // 5. CLEAR SELECTED MOVIE DATA
    //
    // When user starts typing a new query, old TMDB/local
    // selection information must be removed.
    // =========================================================

    function clearSelection() {

        if (selectedMediaType) {
            selectedMediaType.value = "";
        }

        if (selectedSource) {
            selectedSource.value = "";
        }

        if (selectedTmdbId) {
            selectedTmdbId.value = "";
        }
    }


    // =========================================================
    // 6. CLOSE SUGGESTIONS
    // =========================================================

    function closeSuggestions() {

        suggestions.innerHTML = "";

        currentResults = [];

        activeIndex = -1;

        movieInput.removeAttribute(
            "aria-activedescendant"
        );
    }


    // =========================================================
    // 7. FORMAT YEAR
    // =========================================================

    function getYear(movie) {

        if (
            movie.year !== undefined &&
            movie.year !== null &&
            movie.year !== ""
        ) {

            return movie.year;
        }


        if (movie.release_date) {

            return String(
                movie.release_date
            ).slice(0, 4);
        }


        return "N/A";
    }


    // =========================================================
    // 8. FORMAT LANGUAGE
    // =========================================================

    function getLanguage(movie) {

        return (
            movie.language ||
            movie.language_code ||
            movie.original_language ||
            "Unknown"
        );
    }


    // =========================================================
    // 9. FORMAT RATING
    // =========================================================

    function getRating(movie) {

        const value =
            movie.rating ??
            movie.vote_average;


        if (
            value === undefined ||
            value === null ||
            value === ""
        ) {

            return "N/A";
        }


        const numericValue =
            Number(value);


        if (
            !Number.isFinite(
                numericValue
            )
        ) {

            return "N/A";
        }


        return numericValue.toFixed(1);
    }


    // =========================================================
    // 10. FORMAT MEDIA TYPE
    // =========================================================

    function getMediaType(movie) {

        return movie.media_type === "tv"
            ? "TV Series"
            : "Movie";
    }


    // =========================================================
    // 11. SELECT MOVIE
    //
    // IMPORTANT:
    // Clicking a result immediately submits the form.
    // =========================================================

    function selectMovie(movie) {

        if (!movie) {
            return;
        }


        // -----------------------------------------------------
        // Movie title
        // -----------------------------------------------------

        const title =
            movie.title ||
            movie.name ||
            "";


        movieInput.value =
            title;


        // -----------------------------------------------------
        // Media type
        // -----------------------------------------------------

        if (selectedMediaType) {

            selectedMediaType.value =
                movie.media_type ||
                "movie";
        }


        // -----------------------------------------------------
        // Source
        // -----------------------------------------------------

        if (selectedSource) {

            selectedSource.value =
                movie.source ||
                "LOCAL";
        }


        // -----------------------------------------------------
        // TMDB ID
        // -----------------------------------------------------

        if (selectedTmdbId) {

            selectedTmdbId.value =
                movie.tmdb_id ||
                movie.id ||
                "";
        }


        // -----------------------------------------------------
        // Hide suggestions
        // -----------------------------------------------------

        closeSuggestions();


        // -----------------------------------------------------
        // Submit recommendation request
        // -----------------------------------------------------

        submitSearch();
    }


    // =========================================================
    // 12. SUBMIT SEARCH
    // =========================================================

    function submitSearch() {

        const title =
            movieInput.value.trim();


        if (!title) {

            movieInput.focus();

            return;
        }


        if (isSubmitting) {
            return;
        }


        isSubmitting = true;


        // -----------------------------------------------------
        // Disable button while loading
        // -----------------------------------------------------

        if (recommendButton) {

            recommendButton.disabled =
                true;

            recommendButton.innerHTML =
                "<span>⏳ Loading...</span>";
        }


        // -----------------------------------------------------
        // Close dropdown
        // -----------------------------------------------------

        closeSuggestions();


        // -----------------------------------------------------
        // Submit using browser's normal form handling
        // -----------------------------------------------------

        if (
            typeof searchForm.requestSubmit ===
            "function"
        ) {

            searchForm.requestSubmit();

        } else {

            searchForm.submit();
        }
    }


    // =========================================================
    // 13. UPDATE ACTIVE DROPDOWN ITEM
    // =========================================================

    function updateActiveItem() {

        const items =
            suggestions.querySelectorAll(
                ".suggestion"
            );


        items.forEach(
            function (item, index) {

                const isActive =
                    index === activeIndex;


                item.classList.toggle(
                    "active",
                    isActive
                );


                item.setAttribute(
                    "aria-selected",
                    String(isActive)
                );


                if (isActive) {

                    item.scrollIntoView({
                        block: "nearest"
                    });


                    item.id =
                        "watchkaro-suggestion-" +
                        index;


                    movieInput.setAttribute(
                        "aria-activedescendant",
                        item.id
                    );

                } else {

                    item.removeAttribute(
                        "id"
                    );
                }
            }
        );
    }


    // =========================================================
    // 14. RENDER SEARCH RESULTS
    // =========================================================

    function renderSuggestions(movies) {

        suggestions.innerHTML = "";

        currentResults = [];

        activeIndex = -1;


        if (
            !Array.isArray(movies) ||
            movies.length === 0
        ) {

            return;
        }


        // Keep only top 10
        currentResults =
            movies.slice(0, 10);


        // -----------------------------------------------------
        // Create each result
        // -----------------------------------------------------

        currentResults.forEach(
            function (movie, index) {

                const item =
                    document.createElement("div");


                item.className =
                    "suggestion";


                item.dataset.index =
                    String(index);


                item.setAttribute(
                    "role",
                    "option"
                );


                item.setAttribute(
                    "aria-selected",
                    "false"
                );


                const title =
                    movie.title ||
                    movie.name ||
                    "Unknown";


                const year =
                    getYear(movie);


                const language =
                    getLanguage(movie);


                const rating =
                    getRating(movie);


                const mediaType =
                    getMediaType(movie);


                const source =
                    movie.source ||
                    "LOCAL";


                // -------------------------------------------------
                // Dropdown HTML
                // -------------------------------------------------

                item.innerHTML = `

                    <div class="suggestion-top">

                        <div class="suggestion-title">
                            ${escapeHtml(title)}
                        </div>

                        <span class="suggestion-type">
                            ${escapeHtml(mediaType)}
                        </span>

                    </div>


                    <div class="suggestion-info">

                        ${escapeHtml(year)}

                        &nbsp; | &nbsp;

                        ${escapeHtml(language)}

                        &nbsp; | &nbsp;

                        ⭐ ${escapeHtml(rating)}

                        &nbsp; | &nbsp;

                        ${escapeHtml(source)}

                    </div>
                `;


                // =================================================
                // CLICK / MOUSE
                // =================================================

                item.addEventListener(
                    "mousedown",
                    function (event) {

                        /*
                         * Prevent input blur before selection.
                         */

                        event.preventDefault();

                        selectMovie(movie);
                    }
                );


                // =================================================
                // TOUCH
                // =================================================

                item.addEventListener(
                    "touchstart",
                    function () {

                        selectMovie(movie);
                    },
                    {
                        passive: true
                    }
                );


                suggestions.appendChild(
                    item
                );
            }
        );
    }


    // =========================================================
    // 15. SEARCH TMDB + LOCAL DATABASE
    // =========================================================

    async function searchMovies(query) {

        // -----------------------------------------------------
        // Cancel previous request
        // -----------------------------------------------------

        if (currentController) {

            currentController.abort();
        }


        currentController =
            new AbortController();


        // -----------------------------------------------------
        // Create unique request ID
        // -----------------------------------------------------

        const requestId =
            ++requestCounter;


        try {

            const response =
                await fetch(
                    "/search?q=" +
                    encodeURIComponent(
                        query
                    ),
                    {
                        method: "GET",

                        headers: {
                            "Accept":
                                "application/json"
                        },

                        signal:
                            currentController.signal,

                        cache: "no-store"
                    }
                );


            // -------------------------------------------------
            // HTTP ERROR
            // -------------------------------------------------

            if (!response.ok) {

                throw new Error(
                    "Search request failed: " +
                    response.status
                );
            }


            // -------------------------------------------------
            // READ JSON
            // -------------------------------------------------

            const movies =
                await response.json();


            // -------------------------------------------------
            // Ignore old response
            // -------------------------------------------------

            if (
                requestId !==
                requestCounter
            ) {

                return;
            }


            // -------------------------------------------------
            // Render results
            // -------------------------------------------------

            renderSuggestions(
                movies
            );


        } catch (error) {

            // Ignore cancelled requests
            if (
                error &&
                error.name ===
                "AbortError"
            ) {

                return;
            }


            console.error(
                "WatchKaro search error:",
                error
            );


            closeSuggestions();
        }
    }


    // =========================================================
    // 16. INPUT EVENT
    // =========================================================

    movieInput.addEventListener(
        "input",
        function () {

            const query =
                movieInput.value.trim();


            // -------------------------------------------------
            // New typing = new search
            // -------------------------------------------------

            clearSelection();


            // -------------------------------------------------
            // Cancel timer
            // -------------------------------------------------

            clearTimeout(
                searchTimer
            );


            // -------------------------------------------------
            // Cancel old API request
            // -------------------------------------------------

            if (currentController) {

                currentController.abort();

                currentController = null;
            }


            // -------------------------------------------------
            // Clear previous suggestions
            // -------------------------------------------------

            closeSuggestions();


            // -------------------------------------------------
            // Minimum search length
            // -------------------------------------------------

            if (
                query.length < 2
            ) {

                return;
            }


            // -------------------------------------------------
            // Debounce
            // -------------------------------------------------

            searchTimer =
                setTimeout(
                    function () {

                        searchMovies(
                            query
                        );

                    },
                    180
                );
        }
    );


    // =========================================================
    // 17. KEYBOARD NAVIGATION
    // =========================================================

    movieInput.addEventListener(
        "keydown",
        function (event) {

            const hasResults =
                currentResults.length >
                0;


            // -------------------------------------------------
            // ARROW DOWN
            // -------------------------------------------------

            if (
                event.key === "ArrowDown" &&
                hasResults
            ) {

                event.preventDefault();


                activeIndex =
                    (
                        activeIndex + 1
                    ) %
                    currentResults.length;


                updateActiveItem();

                return;
            }


            // -------------------------------------------------
            // ARROW UP
            // -------------------------------------------------

            if (
                event.key === "ArrowUp" &&
                hasResults
            ) {

                event.preventDefault();


                if (
                    activeIndex <= 0
                ) {

                    activeIndex =
                        currentResults.length -
                        1;

                } else {

                    activeIndex--;
                }


                updateActiveItem();

                return;
            }


            // -------------------------------------------------
            // ENTER
            // -------------------------------------------------

            if (
                event.key === "Enter"
            ) {

                // If dropdown result selected
                if (
                    hasResults &&
                    activeIndex >= 0 &&
                    currentResults[
                        activeIndex
                    ]
                ) {

                    event.preventDefault();


                    selectMovie(
                        currentResults[
                            activeIndex
                        ]
                    );


                    return;
                }


                // Otherwise allow normal form submit
                closeSuggestions();

                return;
            }


            // -------------------------------------------------
            // ESCAPE
            // -------------------------------------------------

            if (
                event.key === "Escape"
            ) {

                event.preventDefault();

                closeSuggestions();

                return;
            }
        }
    );


    // =========================================================
    // 18. FORM SUBMISSION
    // =========================================================

    searchForm.addEventListener(
        "submit",
        function (event) {

            const title =
                movieInput.value.trim();


            // -------------------------------------------------
            // Empty search
            // -------------------------------------------------

            if (!title) {

                event.preventDefault();

                movieInput.focus();

                return;
            }


            // -------------------------------------------------
            // Prevent double submit
            // -------------------------------------------------

            if (isSubmitting) {

                return;
            }


            isSubmitting = true;


            // -------------------------------------------------
            // Close suggestions
            // -------------------------------------------------

            closeSuggestions();


            // -------------------------------------------------
            // Loading button
            // -------------------------------------------------

            if (recommendButton) {

                recommendButton.disabled =
                    true;

                recommendButton.innerHTML =
                    "<span>⏳ Loading...</span>";
            }


            /*
             * DO NOT preventDefault().
             *
             * Flask receives the POST request.
             */
        }
    );


    // =========================================================
    // 19. CLICK OUTSIDE SEARCH
    // =========================================================

    document.addEventListener(
        "mousedown",
        function (event) {

            const clickedInput =
                movieInput.contains(
                    event.target
                );


            const clickedSuggestions =
                suggestions.contains(
                    event.target
                );


            if (
                !clickedInput &&
                !clickedSuggestions
            ) {

                closeSuggestions();
            }
        }
    );


    // =========================================================
    // 20. QUICK SEARCH BUTTONS
    //
    // If you keep data-example buttons in HTML, they will work.
    // CSS can hide them if you don't want them visible.
    // =========================================================

    const quickSearchButtons =
        document.querySelectorAll(
            "[data-example]"
        );


    quickSearchButtons.forEach(
        function (button) {

            button.addEventListener(
                "click",
                function () {

                    const title =
                        button.getAttribute(
                            "data-example"
                        );


                    if (!title) {
                        return;
                    }


                    clearSelection();


                    movieInput.value =
                        title;


                    submitSearch();
                }
            );
        }
    );


    // =========================================================
    // 21. RESTORE BUTTON AFTER PAGE CACHE
    // =========================================================

    window.addEventListener(
        "pageshow",
        function () {

            isSubmitting = false;


            if (recommendButton) {

                recommendButton.disabled =
                    false;

                recommendButton.innerHTML =
                    "<span>🔍 WatchKaro</span>";
            }
        }
    );


    // =========================================================
    // 22. DEBUG
    // =========================================================

    console.log(
        "WatchKaro search JavaScript loaded successfully."
    );

})();