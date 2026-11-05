# Week 7: cosine, Euclidean or dot product?

Companion files for the
[week 7 post](https://8567.me/posts/c5-cmovie-wk07-similarity-functions/) of the
cMovie series on [8567.me](https://8567.me). The series teaches Apache
Cassandra® 5.0 features by building movie search for a fictional app called
cMovie, running on Astra DB.

This week you select Cassandra's three similarity scores beside an `ANN OF`
query on `movies`, see why they agree on cMovie's vectors, and use a small
scratch table, `similarity_demo`, to see when they wouldn't. Nothing here
changes the `movies` table or the index you built in week 3.

## Files

| File | What it is |
| --- | --- |
| [`schema.cql`](schema.cql) | The `similarity_demo` scratch table and its three indexes, one per similarity function, plus its three rows |
| [`queries.cql`](queries.cql) | The CQL from the post: the three scores, the scratch table queries, changing a function and the rejected statements |
| [`tools/embed_query.py`](../../tools/embed_query.py) | The query helper. Its new `--scores` option adds the three score columns |

Both `.cql` files are reference copies to paste into the Astra CQL console, one
statement at a time. They aren't scripts to run as a whole, and they don't
create a keyspace. Run `USE default_keyspace;` first, as the post explains.

## What you need

- The `movies` table from weeks 1 to 3, with every movie's `plot_embedding`
  filled in and the vector index from week 3's
  [`schema.cql`](../week-03/schema.cql) built.
- `GEMINI_API_KEY` in your `.env` file. The setup page covers it in
  [section 3](../../docs/setup.md#3-google-ai-studio).

From the root of the repo, in your virtual environment, print the statement:

```bash
python tools/embed_query.py "an old man and his machine become unlikely friends" --scores
```

The statement is long, because it carries the query vector four times. Paste it
as it is.

Each run sends your phrase to Google's Gemini API once. Google's terms for the
free tier allow it to use what you send to improve its products and to let human
reviewers read it, so only type phrases you'd be happy for Google to see.

The setup page covers the database, token and loader:
[`docs/setup.md`](../../docs/setup.md).

## Data and licences

Film facts come from [Wikidata](https://www.wikidata.org), released under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). Plot text comes
from the [English Wikipedia](https://en.wikipedia.org), released under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The scores
describe the plot embeddings, and nothing here returns plot text.

## Disclosure

> This series is based on new features in Apache Cassandra® 5.0 but the
> tutorials are run on DataStax Astra DB to make it simpler for developers to
> build apps without having to worry about installing/configuring a cluster. For
> full disclosure, I'm an Apache Cassandra committer and a Developer Advocate at
> DataStax, now an IBM company.

*Apache Cassandra, Cassandra, Apache, the Apache logo, and the Apache Cassandra
project logo are either registered trademarks or trademarks of The Apache
Software Foundation in the United States and other countries.*
