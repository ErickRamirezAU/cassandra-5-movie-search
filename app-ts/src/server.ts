import Fastify from "fastify";
import { types } from "cassandra-driver";
import { createClient } from "./connection.js";

const client = createClient();
await client.connect();

const SEED_QUERY = "SELECT movie_id, title, release_year, plot_embedding FROM movies WHERE movie_id = ?";

// ANN OF takes the seed's own Vector object. The driver rejects a plain number[].
const SIMILAR_QUERY =
  "SELECT movie_id, title, release_year, similarity_cosine(plot_embedding, ?) AS score " +
  "FROM movies ORDER BY plot_embedding ANN OF ? LIMIT ?";

const app = Fastify({ logger: true });

app.get<{
  Params: { id: string };
  Querystring: { limit: number; within: number };
}>(
  "/movies/:id/similar",
  {
    schema: {
      querystring: {
        type: "object",
        properties: {
          limit: { type: "integer", minimum: 1, maximum: 50, default: 10 },
          within: { type: "number", minimum: 0, maximum: 1, default: 0.02 },
        },
      },
    },
  },
  async (request, reply) => {
    const { id } = request.params;
    const { limit, within } = request.query;

    const seedResult = await client.execute(SEED_QUERY, [id], { prepare: true });
    const seed = seedResult.first();
    if (!seed) {
      return reply.code(404).send({ error: `No movie with id ${id}` });
    }

    // Ask for one extra row: the seed is its own best match and is dropped below.
    const result = await client.execute(
      SIMILAR_QUERY,
      [seed.plot_embedding, seed.plot_embedding, limit + 1],
      { prepare: true, consistency: types.consistencies.one },
    );

    const neighbours = result.rows
      .filter((row) => row.movie_id !== id)
      .map((row) => ({
        movie_id: row.movie_id as string,
        title: row.title as string,
        release_year: row.release_year as number,
        score: Number((row.score as number).toFixed(4)),
      }));

    // CQL can't filter on a score, so the threshold is applied here. It is
    // relative to the best neighbour because the scores bunch together.
    const best = neighbours[0]?.score ?? 0;
    const results = neighbours.filter((n) => n.score >= best - within);

    return {
      seed: { movie_id: id, title: seed.title as string, release_year: seed.release_year as number },
      results,
    };
  },
);

await app.listen({ port: 8000, host: "127.0.0.1" });
