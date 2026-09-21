-- Week 1: the movies table and its Storage Attached Indexes.
--
-- Paste these statements into the Astra CQL console, after selecting the
-- keyspace Astra provides:
--
--   USE default_keyspace;
--
-- The loader creates this table too (CREATE TABLE IF NOT EXISTS), so creating it
-- first is safe. The columns and their order match the loader's table.

CREATE TABLE IF NOT EXISTS movies (
    movie_id text PRIMARY KEY,
    title text,
    release_year int,
    genres set<text>,
    runtime int,
    cmovie_rating float,
    cmovie_votes int,
    cmovie_popularity float,
    plot text,
    actors list<text>,
    actor_words set<text>,
    title_words set<text>
);

-- One SAI index per column you want to query. Run these after the loader has
-- finished. Astra builds each index in the background, and a query that uses an
-- index fails with INDEX_BUILD_IN_PROGRESS until its build completes.

CREATE CUSTOM INDEX ON movies (release_year) USING 'StorageAttachedIndex';
CREATE CUSTOM INDEX ON movies (cmovie_rating) USING 'StorageAttachedIndex';
CREATE CUSTOM INDEX ON movies (runtime) USING 'StorageAttachedIndex';
CREATE CUSTOM INDEX ON movies (genres) USING 'StorageAttachedIndex';
