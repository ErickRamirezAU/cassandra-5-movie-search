-- Week 1: querying cMovie with Storage Attached Indexes.
--
-- Paste one statement at a time into the Astra CQL console, after selecting
-- the keyspace Astra provides:
--
--   USE default_keyspace;

-- Confirm the load.
SELECT movie_id, title, release_year FROM movies LIMIT 5;

-- Before you create the indexes: this is rejected with "Cannot execute this
-- query as it might involve data filtering", because release_year isn't part
-- of the primary key and has no index yet.
SELECT title, release_year FROM movies WHERE release_year = 1999;

-- After you create the indexes in schema.cql:

-- 1. Films released in 1999
SELECT title, release_year FROM movies WHERE release_year = 1999 LIMIT 10;

-- 2. Films released between 2010 and 2019
SELECT title, release_year FROM movies
WHERE release_year >= 2010 AND release_year <= 2019 LIMIT 10;

-- 3. Films rated 7.5 or higher in cMovie
SELECT title, cmovie_rating FROM movies WHERE cmovie_rating >= 7.5 LIMIT 10;

-- 4. Science fiction films
SELECT title, genres FROM movies WHERE genres CONTAINS 'science fiction' LIMIT 10;

-- 5. Science fiction films from the 2010s rated above 7
SELECT title, release_year, cmovie_rating FROM movies
WHERE genres CONTAINS 'science fiction'
  AND release_year >= 2010 AND release_year <= 2019
  AND cmovie_rating > 7 LIMIT 10;

-- 6. Short dramas under 100 minutes
SELECT title, runtime FROM movies
WHERE genres CONTAINS 'drama' AND runtime < 100 LIMIT 10;

-- The deliberate failure: cmovie_votes has no index, so restricting on it
-- brings the ALLOW FILTERING error back.
SELECT title FROM movies WHERE release_year = 1999 AND cmovie_votes > 5000;
