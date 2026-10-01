-- Week 3: the plot_embedding vector column and its SAI vector index.
--
-- Paste these statements into the Astra CQL console, after selecting the
-- keyspace Astra provides:
--
--   USE default_keyspace;
--
-- If you loaded cMovie in week 1 or 2, your movies table doesn't have the two
-- new columns yet. Add them first. Weeks 1 and 2 showed the table as it was
-- then, so tutorials/week-01/schema.cql is unchanged. A fresh run of the
-- loader (tools/loader.py) creates the table with both columns already.

ALTER TABLE movies ADD (plot_embedding vector<float, 3072>, wikipedia_url text);

-- Then fill them from the repo's root directory. It's safe to run again if it
-- stops part-way, because it skips movies that already have an embedding:
--
--   python tools/loader.py --backfill

-- The vector index. Run this after the backfill has finished. Astra DB builds
-- the index in the background, and a vector query fails with a ReadFailure
-- error until the build completes. similarity_function defaults to COSINE
-- anyway. It's written out so the choice is visible. Week 7 covers the others.
--
-- On Cassandra 5.0 this statement returns a client warning that SAI ANN
-- indexes on vector columns are experimental and not recommended for
-- production use. Astra DB doesn't show that warning.

CREATE CUSTOM INDEX ON movies (plot_embedding) USING 'StorageAttachedIndex'
WITH OPTIONS = {'similarity_function': 'COSINE'};
