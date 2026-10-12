-- Week 4: the CQL behind GET /movies/semantic, and what it rejects.
--
-- Paste one statement at a time into the Astra CQL console, after selecting
-- the keyspace Astra provides:
--
--   USE default_keyspace;
--
-- Each ANN OF statement needs a vector of 3,072 numbers, about 37 KB of text,
-- so it's shown here with [...] in its place. Let tools/embed_query.py write
-- the complete statement for any phrase, then paste what it prints:
--
--   python tools/embed_query.py "a heist that goes wrong"
--
-- The API doesn't paste vectors. It prepares the statement once and binds
-- the vector and the LIMIT, so it needs no console and no copying.

-- 1. The query the API runs, with the vector bound to the first ?
SELECT title, release_year FROM movies
ORDER BY plot_embedding ANN OF [...] LIMIT 5;

-- 2. A deliberate failure: ANN OF with no LIMIT
SELECT title, release_year FROM movies
ORDER BY plot_embedding ANN OF [...];

-- 3. A deliberate failure: a LIMIT above 1,000
SELECT title, release_year FROM movies
ORDER BY plot_embedding ANN OF [...] LIMIT 1001;
