# Week 3: vector search in the same table as your data

Companion files for the week 3 post of the cMovie series on
[8567.me](https://8567.me). The series teaches Apache Cassandra® 5.0 features by
building movie search for a fictional app called cMovie, running on Astra DB.

This week you add a `plot_embedding` column of type `vector<float, 3072>` to
the `movies` table, index it with a Storage Attached Index and search it with
`ORDER BY ... ANN OF ... LIMIT`, so a reader can search by what a movie's plot
means instead of the words it uses.

## Files

| File | What it is |
| --- | --- |
| [`schema.cql`](schema.cql) | The `ALTER TABLE` for the two new columns and the SAI vector index |
| [`queries.cql`](queries.cql) | The queries from the post, including the deliberate failure |

The CQL files are reference copies to paste into the Astra CQL console, one
statement at a time. They aren't scripts to run as a whole. Neither file
creates a keyspace. Run `USE default_keyspace;` first, as the post explains.

## What you need

- The `movies` table from week 1 or 2, loaded with `tools/loader.py`.
- A Google AI Studio API key in your `.env` file as `GEMINI_API_KEY`. The
  setup page covers it in
  [section 3](../../docs/setup.md#3-google-ai-studio).

Fill the new columns from the root of the repo, in your virtual environment:

```bash
python tools/loader.py --backfill
```

Google's free tier counts every plot you send, not just every request, so a
full set of 1,000 plots can use up a day's allowance. If the run stops, run the
same command again later. It carries on with the movies that have no embedding
yet. `--max-embeddings 300` caps a run if you'd rather spread it out.

To turn a search phrase into a query you can paste into the CQL console:

```bash
python tools/embed_query.py "a retired hitman is pulled back in"
```

The setup page covers the database, token and loader:
[`docs/setup.md`](../../docs/setup.md).

## Data and licences

Film facts come from [Wikidata](https://www.wikidata.org), released under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). Plot text comes
from the [English Wikipedia](https://en.wikipedia.org), released under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), and each
movie's `wikipedia_url` column links to its article. The `cmovie_rating`,
`cmovie_votes` and `cmovie_popularity` columns are fictional numbers generated
by the loader, not real audience scores.

Plot text is sent to Google's Gemini API to create the embeddings. Google's
terms for the free tier allow it to use what you send to improve its products
and to let human reviewers read it, so only public Wikipedia plot text and
your own search phrases are sent.

## Disclosure

> This series is based on new features in Apache Cassandra® 5.0 but the
> tutorials are run on DataStax Astra DB to make it simpler for developers to
> build apps without having to worry about installing/configuring a cluster. For
> full disclosure, I'm an Apache Cassandra committer and a Developer Advocate at
> DataStax, now an IBM company.

*Apache Cassandra, Cassandra, Apache, the Apache logo, and the Apache Cassandra
project logo are either registered trademarks or trademarks of The Apache
Software Foundation in the United States and other countries.*
