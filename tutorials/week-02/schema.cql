-- Week 2: the search indexes on title_words and actor_words.
--
-- Paste these statements into the Astra CQL console, after selecting the
-- keyspace Astra provides:
--
--   USE default_keyspace;
--
-- The movies table and its title_words/actor_words columns already exist
-- from week 1's schema.cql, and the loader has been filling both columns
-- since the first load. This file only adds the two new indexes.

CREATE CUSTOM INDEX ON movies (title_words) USING 'StorageAttachedIndex';
CREATE CUSTOM INDEX ON movies (actor_words) USING 'StorageAttachedIndex';
