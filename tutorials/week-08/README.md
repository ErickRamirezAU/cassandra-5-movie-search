# Week 8: "more like this" recommendations in TypeScript

Companion files for the
[week 8 post](https://8567.me/posts/c5-cmovie-wk08-more-like-this/) of the
cMovie series on [8567.me](https://8567.me). The series teaches Apache
Cassandra® 5.0 features by building movie search for a fictional app called
cMovie, running on Astra DB.

This week you build a Fastify API in TypeScript. `GET /movies/:id/similar`
reads a film's stored plot vector, finds its nearest neighbours with
`ORDER BY ... ANN OF`, returns each one's similarity score, and applies a
score threshold in code, because CQL can't filter on a score. It makes no
Gemini calls.

## Files

| File | What it is |
| --- | --- |
| [`queries.cql`](queries.cql) | The CQL the endpoint runs, for trying in the Astra CQL console |
| [`app-ts/src/connection.ts`](../../app-ts/src/connection.ts) | Connects to Astra DB, or to a Cassandra cluster when `CASSANDRA_HOSTS` is set |
| [`app-ts/src/server.ts`](../../app-ts/src/server.ts) | The Fastify server and the one endpoint |

## What you need

- Weeks 1 to 3 done: the `movies` table with every `plot_embedding` filled in
  and the week 3 vector index.
- Node 20.11 or later.
- The `.env` file and the secure connect bundle from the
  [setup page](../../docs/setup.md), in the repo's root directory. No Google AI
  Studio key is used this week.

## Run it

From the repo root:

```bash
cd app-ts
npm install
npm start
```

Then, in another terminal:

```bash
curl "http://127.0.0.1:8000/movies/Q788822/similar?limit=5"
```

`Q788822` is Her. `limit` is how many films to return (1 to 50, default 10).
`within` is how far below the best neighbour's score a film may fall and still
be kept (default 0.02).
