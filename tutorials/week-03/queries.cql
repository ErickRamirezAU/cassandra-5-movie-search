-- Week 3: searching cMovie by what a plot means, with ORDER BY ... ANN OF.
--
-- Paste one statement at a time into the Astra CQL console, after selecting
-- the keyspace Astra provides:
--
--   USE default_keyspace;
--
-- Run these after the vector index in schema.cql has finished building.
--
-- Each ANN OF statement needs a vector of 3,072 numbers, about 37 KB of text,
-- so it's shown here with [...] in its place. Let tools/embed_query.py write
-- the complete statement for any phrase, then paste what it prints:
--
--   python tools/embed_query.py "a retired hitman is pulled back in"
--
-- Your results may differ slightly from the post. Vector search is
-- approximate, and your table holds whatever movies your own loader run
-- picked.

-- 1. Movies about a retired hitman who is pulled back in
SELECT title, release_year FROM movies
ORDER BY plot_embedding ANN OF [...] LIMIT 5;

-- 2. A different kind of phrase: a lonely robot learns to love
SELECT title, release_year FROM movies
ORDER BY plot_embedding ANN OF [...] LIMIT 5;

-- 3. The first phrase reworded: an ex-assassin is dragged back into his old life
SELECT title, release_year FROM movies
ORDER BY plot_embedding ANN OF [...] LIMIT 5;

-- 4. A deliberate failure: a query vector with 3 numbers instead of 3,072
SELECT title, release_year FROM movies
ORDER BY plot_embedding ANN OF [0.1, 0.2, 0.3] LIMIT 5;
