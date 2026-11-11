// Shared database connection for the TypeScript apps. Mirrors tools/connection.py:
// Astra DB when ASTRA_DB_TOKEN is set, otherwise a plain Cassandra cluster
// from CASSANDRA_HOSTS (host or host:port, comma separated).
import { Client } from "cassandra-driver";
import { resolve } from "node:path";

export function createClient(): Client {
  const env = process.env;
  const keyspace = env.ASTRA_DB_KEYSPACE || env.CASSANDRA_KEYSPACE || "default_keyspace";

  if (env.ASTRA_DB_TOKEN) {
    return new Client({
      cloud: {
        secureConnectBundle: resolve(import.meta.dirname, "../../secure-connect-cmovies.zip"),
      },
      credentials: { username: "token", password: env.ASTRA_DB_TOKEN },
      keyspace,
    });
  }

  if (!env.CASSANDRA_HOSTS) {
    throw new Error("Set ASTRA_DB_TOKEN (Astra DB) or CASSANDRA_HOSTS (a Cassandra cluster) in .env");
  }
  const port = Number(env.CASSANDRA_CLIENT_PORT || 9042);
  const contactPoints = env.CASSANDRA_HOSTS.split(",").map((h) => {
    const host = h.trim();
    return host.includes(":") ? host : `${host}:${port}`;
  });
  return new Client({
    contactPoints,
    localDataCenter: env.CASSANDRA_LOCAL_DC || "datacenter1",
    keyspace,
    credentials:
      env.CASSANDRA_USERNAME && env.CASSANDRA_PASSWORD
        ? { username: env.CASSANDRA_USERNAME, password: env.CASSANDRA_PASSWORD }
        : undefined,
  });
}
