-- Week 5: filtered vector search. The CQL from the post, and the filter
-- Cassandra rejects.
--
-- Paste one statement at a time into the Astra CQL console, after selecting
-- the keyspace Astra provides:
--
--   USE default_keyspace;
--
-- Each ANN OF statement needs a vector of 3,072 numbers, about 37 KB of text,
-- so it's shown here with [...] in its place. Let tools/embed_query.py write
-- the complete statement for the phrase, then paste what it prints:
--
--   python tools/embed_query.py "a lonely robot learns what love is" --limit 10
--
-- Embed the phrase once and reuse the vector: edit the WHERE clause of the
-- statement you already have. The helper can also write the WHERE clause:
--
--   python tools/embed_query.py "a lonely robot learns what love is" \
--     --limit 10 --where "genres CONTAINS 'science fiction'"
--
-- This week adds no index. The filters use the SAI indexes from weeks 1 to 3.

-- 1. Baseline: pure vector search, no filter
SELECT title, release_year FROM movies
ORDER BY plot_embedding ANN OF [...] LIMIT 10;

-- 2. What the genres labels look like
SELECT title, release_year, genres FROM movies
WHERE genres CONTAINS 'science fiction' LIMIT 1;

-- 3. Add a genre. The WHERE clause goes before ORDER BY
SELECT title, release_year FROM movies
WHERE genres CONTAINS 'science fiction'
ORDER BY plot_embedding ANN OF [...] LIMIT 10;

-- 4. The label is case sensitive: this returns 0 rows, not an error
SELECT title, release_year FROM movies
WHERE genres CONTAINS 'Science Fiction' LIMIT 3;

-- 5. Add a decade
SELECT title, release_year FROM movies
WHERE genres CONTAINS 'science fiction'
AND release_year >= 2010 AND release_year < 2020
ORDER BY plot_embedding ANN OF [...] LIMIT 10;

-- 6. Add a minimum rating (cmovie_rating is a fictional in-app number)
SELECT title, release_year, cmovie_rating FROM movies
WHERE genres CONTAINS 'science fiction'
AND release_year >= 2010 AND release_year < 2020
AND cmovie_rating >= 7.0
ORDER BY plot_embedding ANN OF [...] LIMIT 10;

-- 7. A narrow filter: only 4 movies carry the boxing label, so LIMIT 50
--    returns 4 rows
SELECT title, release_year, genres FROM movies
WHERE genres CONTAINS 'boxing'
ORDER BY plot_embedding ANN OF [...] LIMIT 50;

-- 8. A deliberate failure: cmovie_votes has no SAI index
SELECT title, release_year FROM movies
WHERE genres CONTAINS 'science fiction' AND cmovie_votes > 1000
ORDER BY plot_embedding ANN OF [...] LIMIT 10;

-- 9. A deliberate failure: ALLOW FILTERING doesn't help
SELECT title, release_year FROM movies
WHERE genres CONTAINS 'science fiction' AND cmovie_votes > 1000
ORDER BY plot_embedding ANN OF [...] LIMIT 10 ALLOW FILTERING;
