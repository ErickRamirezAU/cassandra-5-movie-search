-- Week 6: the CQL behind GET /movies/assist.
--
-- Paste one statement at a time into the Astra CQL console, after selecting
-- the keyspace Astra provides:
--
--   USE default_keyspace;
--
-- Each ANN OF statement needs a vector of 3,072 numbers, about 37 KB of text,
-- so it's shown here with [...] in its place. Let tools/embed_query.py write
-- the complete statement for a phrase, then paste what it prints:
--
--   python tools/embed_query.py "A nostalgic adventure" --limit 3 \
--     --where "genres CONTAINS 'family' AND release_year >= 1990 AND release_year <= 1999" \
--     --columns "title, release_year, genres, cmovie_rating, plot, wikipedia_url"
--
-- The API doesn't paste vectors. It binds the vector to the final placeholder
-- and sends the statement at consistency level ONE, so it needs no console.

-- 1. No filters: the 3 movies closest in meaning to the request
SELECT title, release_year, genres, cmovie_rating, plot, wikipedia_url
FROM movies
ORDER BY plot_embedding ANN OF [...] LIMIT 3;

-- 2. With the filters the endpoint accepts: genre, years and a minimum rating.
--    The API adds each clause only when the caller sends that parameter
SELECT title, release_year, genres, cmovie_rating, plot, wikipedia_url
FROM movies
WHERE genres CONTAINS 'family'
AND release_year >= 1990 AND release_year <= 1999
ORDER BY plot_embedding ANN OF [...] LIMIT 3;

-- 3. Filters that match nothing return 0 rows, not an error. The API sees
--    the empty result and answers without calling Gemini
SELECT title, release_year, genres, cmovie_rating, plot, wikipedia_url
FROM movies
WHERE genres CONTAINS 'western' AND release_year >= 2020
ORDER BY plot_embedding ANN OF [...] LIMIT 3;
