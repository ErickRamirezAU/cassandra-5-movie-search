# Week 1: Find films by genre, year and rating with SAI

Companion files for the week 1 post of the cMovie series on
[8567.me](https://8567.me). The series teaches Apache Cassandra® 5.0 features by
building movie search for a fictional app called cMovie, running on Astra DB.

This week you create the `movies` table, load 1,000 films with the loader in
[`tools/`](../../tools), add Storage Attached Indexes (SAI) and query the table
by year, rating, runtime and genre without `ALLOW FILTERING`.

## Files

| File | What it is |
| --- | --- |
| [`schema.cql`](schema.cql) | The `movies` table and the four SAI indexes |
| [`queries.cql`](queries.cql) | The queries from the post, including the deliberate failure |

These are reference copies to paste into the Astra CQL console, one statement at
a time. They aren't scripts to run as a whole. Neither file creates a keyspace.
Run `USE default_keyspace;` first, as the post explains.

The setup page covers the database, token and loader:
[`docs/setup.md`](../../docs/setup.md).

## Data and licences

Film facts come from [Wikidata](https://www.wikidata.org), released under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). Plot text comes
from the [English Wikipedia](https://en.wikipedia.org), released under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The plots
aren't queried this week, but they're in your table. The `cmovie_rating`,
`cmovie_votes` and `cmovie_popularity` columns are fictional numbers generated
by the loader, not real audience scores.

## Disclosure

> This series is based on new features in Apache Cassandra® 5.0 but the
> tutorials are run on DataStax Astra DB to make it simpler for developers to
> build apps without having to worry about installing/configuring a cluster. For
> full disclosure, I'm an Apache Cassandra committer and a Developer Advocate at
> DataStax, now an IBM company.

*Apache Cassandra, Cassandra, Apache, the Apache logo, and the Apache Cassandra
project logo are either registered trademarks or trademarks of The Apache
Software Foundation in the United States and other countries.*
