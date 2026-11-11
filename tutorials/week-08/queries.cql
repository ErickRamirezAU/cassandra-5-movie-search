-- Week 8: the two statements app-ts/src/server.ts runs for GET /movies/:id/similar.
-- Reference copies. Run USE default_keyspace; first, one statement at a time.

-- 1. Find a seed film's id by title word (the week 2 index).
SELECT movie_id, title, release_year FROM movies WHERE title_words CONTAINS 'her' LIMIT 20;

-- 2. Read the seed's stored vector. The app binds this Vector straight into step 3.
SELECT movie_id, title, release_year, plot_embedding FROM movies WHERE movie_id = 'Q788822';

-- 3. Neighbours with a score. The app passes the seed's vector twice and asks for
-- limit + 1 rows, because the seed is its own best match (score 1). In the CQL
-- console, use embed_query.py's output or paste a vector in place of the markers.
-- SELECT movie_id, title, release_year, similarity_cosine(plot_embedding, <vector>) AS score
-- FROM movies ORDER BY plot_embedding ANN OF <vector> LIMIT 11;
