-- Week 7: similarity functions. The CQL from the post.
--
-- Paste one statement at a time into the Astra CQL console, after selecting
-- the keyspace Astra provides:
--
--   USE default_keyspace;
--
-- Part 1 runs against movies. Each ANN OF statement needs a vector of 3,072
-- numbers, about 37 KB of text, so it's shown here with [...] in its place.
-- With --scores the helper writes the vector into each score column too, so
-- the statement it prints is about 150 KB. Paste it as it is:
--
--   python tools/embed_query.py "an old man and his machine become unlikely friends" --scores
--
-- Part 2 runs against the scratch table in schema.cql.

-- 1. Part 1: three scores for each of the top 5 movies
SELECT title, release_year,
       similarity_cosine(plot_embedding, [...]) AS cosine,
       similarity_euclidean(plot_embedding, [...]) AS euclidean,
       similarity_dot_product(plot_embedding, [...]) AS dot_product
FROM movies
ORDER BY plot_embedding ANN OF [...] LIMIT 5;

-- 2. Part 2: the same query vector against each index
SELECT id FROM similarity_demo ORDER BY v_cosine ANN OF [1.0, 0.0, 0.0] LIMIT 3;
SELECT id FROM similarity_demo ORDER BY v_dot_product ANN OF [1.0, 0.0, 0.0] LIMIT 3;
SELECT id FROM similarity_demo ORDER BY v_euclidean ANN OF [1.0, 0.0, 0.0] LIMIT 3;

-- 3. Change a function: drop the index first
DROP INDEX similarity_demo_dot_product_idx;

-- 4. Rejected: a function Cassandra doesn't know
CREATE CUSTOM INDEX similarity_demo_dot_product_idx
    ON similarity_demo (v_dot_product) USING 'StorageAttachedIndex'
    WITH OPTIONS = {'similarity_function': 'MANHATTAN'};

-- 5. Then create it again with a valid one
CREATE CUSTOM INDEX similarity_demo_dot_product_idx
    ON similarity_demo (v_dot_product) USING 'StorageAttachedIndex'
    WITH OPTIONS = {'similarity_function': 'COSINE'};

-- 6. Rejected: a similarity function in WHERE
SELECT title FROM movies
WHERE similarity_cosine(plot_embedding, [...]) > 0.8 LIMIT 5;

-- 7. Tidy up
DROP TABLE similarity_demo;
