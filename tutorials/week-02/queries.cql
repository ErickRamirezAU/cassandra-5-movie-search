-- Week 2: fragment search on title_words and actor_words, combined with
-- week 1's filters.
--
-- Paste one statement at a time into the Astra CQL console, after selecting
-- the keyspace Astra provides:
--
--   USE default_keyspace;
--
-- Run these after the indexes in schema.cql have finished building.

-- 1. Films with "wick" in the title
SELECT title FROM movies WHERE title_words CONTAINS 'wick' LIMIT 10;

-- 2. Films with a Murphy in the cast
SELECT title, actors FROM movies WHERE actor_words CONTAINS 'murphy' LIMIT 10;

-- 3. Films with "chapter" and "wick" in the title
SELECT title FROM movies
WHERE title_words CONTAINS 'wick' AND title_words CONTAINS 'chapter' LIMIT 10;

-- 4. Films with a Murphy in the cast released after 2015
SELECT title, release_year, actors FROM movies
WHERE actor_words CONTAINS 'murphy' AND release_year > 2015 LIMIT 10;

-- 5. Highly rated dramas with "wick" in the title (an empty result, not an
-- error)
SELECT title FROM movies
WHERE genres CONTAINS 'drama' AND title_words CONTAINS 'wick'
  AND cmovie_rating > 7 LIMIT 10;

-- 6. Films with "murphy" in the cast, science fiction genre, rated above 7
SELECT title, actors, genres, cmovie_rating FROM movies
WHERE actor_words CONTAINS 'murphy' AND genres CONTAINS 'science fiction'
  AND cmovie_rating > 7 LIMIT 10;

-- The deliberate failure: cmovie_votes has no index, so restricting on it
-- brings the ALLOW FILTERING error back.
SELECT title FROM movies
WHERE actor_words CONTAINS 'murphy' AND cmovie_votes > 5000;
