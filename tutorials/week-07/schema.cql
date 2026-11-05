-- Week 7: a three row scratch table for comparing similarity functions.
--
-- Paste these statements into the Astra CQL console, after selecting the
-- keyspace Astra provides:
--
--   USE default_keyspace;
--
-- This table is separate from movies, so nothing here touches the index you
-- built in week 3. Each of the three vector columns holds the same vectors
-- and has its own SAI vector index, because one column can have only one
-- vector index.

CREATE TABLE IF NOT EXISTS similarity_demo (
    id text PRIMARY KEY,
    v_cosine vector<float, 3>,
    v_dot_product vector<float, 3>,
    v_euclidean vector<float, 3>
);

CREATE CUSTOM INDEX IF NOT EXISTS similarity_demo_cosine_idx
    ON similarity_demo (v_cosine) USING 'StorageAttachedIndex'
    WITH OPTIONS = {'similarity_function': 'COSINE'};

CREATE CUSTOM INDEX IF NOT EXISTS similarity_demo_dot_product_idx
    ON similarity_demo (v_dot_product) USING 'StorageAttachedIndex'
    WITH OPTIONS = {'similarity_function': 'DOT_PRODUCT'};

CREATE CUSTOM INDEX IF NOT EXISTS similarity_demo_euclidean_idx
    ON similarity_demo (v_euclidean) USING 'StorageAttachedIndex'
    WITH OPTIONS = {'similarity_function': 'EUCLIDEAN'};

-- Three vectors, none of them length 1. A points exactly the way the query
-- will point but is short. B is long and points slightly off. C is close to
-- the query in space but points a little off too.
INSERT INTO similarity_demo (id, v_cosine, v_dot_product, v_euclidean)
VALUES ('A', [0.2, 0.0, 0.0], [0.2, 0.0, 0.0], [0.2, 0.0, 0.0]);
INSERT INTO similarity_demo (id, v_cosine, v_dot_product, v_euclidean)
VALUES ('B', [10.0, 1.0, 0.0], [10.0, 1.0, 0.0], [10.0, 1.0, 0.0]);
INSERT INTO similarity_demo (id, v_cosine, v_dot_product, v_euclidean)
VALUES ('C', [0.9, 0.3, 0.0], [0.9, 0.3, 0.0], [0.9, 0.3, 0.0]);
