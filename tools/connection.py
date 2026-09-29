"""
Shared database connection for the loader and every tutorial app.

Reads .env in the repo's root directory (or the current environment) and
connects to one of two backends:

- Astra DB, when ASTRA_DB_TOKEN is set, through the Secure Connect Bundle
  saved in the repo's root directory.
- A plain Apache Cassandra cluster, when there's no Astra token and
  CASSANDRA_HOSTS is set, with optional CASSANDRA_USERNAME/PASSWORD,
  CASSANDRA_KEYSPACE and CASSANDRA_CLIENT_PORT.

Either backend defaults its keyspace to "default_keyspace" if one isn't set.
Only the loader creates that keyspace on a plain cluster. The apps expect it,
and the movies table in it, to exist already.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_DIR / ".env"
DEFAULT_SCB_PATH = PROJECT_DIR / "secure-connect-cmovies.zip"

KEYSPACE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env(path: Path) -> dict:
    values = dict(os.environ)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


def build_config(env_file=DEFAULT_ENV_PATH, scb=None, keyspace=None) -> dict:
    env_path = Path(env_file)
    env = load_env(env_path)

    if env.get("ASTRA_DB_TOKEN"):
        scb_path = Path(scb or DEFAULT_SCB_PATH)
        if not scb_path.exists():
            sys.exit(
                f"Secure Connect Bundle not found at {scb_path}. Download it from your "
                f"Astra DB dashboard and save it in the project's root directory "
                f"({PROJECT_DIR}) as {DEFAULT_SCB_PATH.name} -- or, if your database isn't "
                f"named '{DEFAULT_SCB_PATH.stem.removeprefix('secure-connect-')}', pass its "
                f"bundle's path to the loader with --scb /path/to/your-bundle.zip."
            )
        return {
            "backend": "astra",
            "token": env["ASTRA_DB_TOKEN"],
            "keyspace": keyspace or env.get("ASTRA_DB_KEYSPACE") or "default_keyspace",
            "scb_path": scb_path,
        }

    # No Astra token: fall back to a plain Cassandra cluster via CASSANDRA_*.
    hosts_raw = env.get("CASSANDRA_HOSTS")
    if not hosts_raw:
        env_file_state = "found" if env_path.exists() else "NOT FOUND"
        sys.exit(
            f"No backend configured: ASTRA_DB_TOKEN and CASSANDRA_HOSTS are both unset. "
            f"Checked env file {env_path} ({env_file_state}) and the current environment. Set one: "
            "ASTRA_DB_TOKEN (+ optionally ASTRA_DB_KEYSPACE) for Astra, or CASSANDRA_HOSTS "
            "(+ optionally CASSANDRA_USERNAME/PASSWORD/KEYSPACE/CLIENT_PORT) for a plain cluster."
        )

    default_port = int(env.get("CASSANDRA_CLIENT_PORT") or 9042)
    hosts = []
    for entry in hosts_raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        host, _, port = entry.partition(":")
        hosts.append((host, int(port) if port else default_port))
    if not hosts:
        sys.exit(f"CASSANDRA_HOSTS in {env_file} is empty")

    username = env.get("CASSANDRA_USERNAME") or None
    password = env.get("CASSANDRA_PASSWORD") or None
    if bool(username) != bool(password):
        sys.exit("Set both CASSANDRA_USERNAME and CASSANDRA_PASSWORD, or leave both empty for an unauthenticated cluster")

    keyspace = keyspace or env.get("CASSANDRA_KEYSPACE") or "default_keyspace"
    if not KEYSPACE_NAME_RE.match(keyspace):
        sys.exit(f"'{keyspace}' isn't a valid keyspace name")

    return {
        "backend": "cassandra",
        "hosts": hosts,
        "username": username,
        "password": password,
        "keyspace": keyspace,
    }


def connect(cfg: dict, create_keyspace: bool = False):
    from cassandra import ConsistencyLevel
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile

    # Astra rejects ANY/ONE/LOCAL_ONE for writes outright ("Provided value
    # ONE is not allowed for Write Consistency Level"); LOCAL_QUORUM is a
    # sane default consistency on Astra DB and Cassandra alike, so it's
    # used here regardless of backend. Set via an execution
    # profile, not session.default_consistency_level, which the driver
    # deprecates in favour of this.
    profile = ExecutionProfile(consistency_level=ConsistencyLevel.LOCAL_QUORUM)
    execution_profiles = {EXEC_PROFILE_DEFAULT: profile}

    if cfg["backend"] == "astra":
        cloud_config = {"secure_connect_bundle": str(cfg["scb_path"])}
        auth_provider = PlainTextAuthProvider(username="token", password=cfg["token"])
        cluster = Cluster(cloud=cloud_config, auth_provider=auth_provider, execution_profiles=execution_profiles)
        session = cluster.connect(cfg["keyspace"])
        return cluster, session

    # Plain Cassandra: DefaultEndPoint gives each contact point its own port
    # (CASSANDRA_HOSTS entries can be "host" or "host:port", falling back to
    # CASSANDRA_CLIENT_PORT). No local-DC var: LOCAL_QUORUM above relies on
    # DCAwareRoundRobinPolicy's documented behaviour of inferring local_dc
    # from the first contact point that resolves, which is fine as long as
    # every contact point is in one DC -- true for the single-node/single-DC
    # cluster this tutorial path targets.
    from cassandra.connection import DefaultEndPoint

    auth_provider = None
    if cfg["username"] and cfg["password"]:
        auth_provider = PlainTextAuthProvider(username=cfg["username"], password=cfg["password"])
    endpoints = [DefaultEndPoint(host, port) for host, port in cfg["hosts"]]
    cluster = Cluster(contact_points=endpoints, auth_provider=auth_provider, execution_profiles=execution_profiles)
    session = cluster.connect()
    if create_keyspace:
        session.execute(
            f"CREATE KEYSPACE IF NOT EXISTS {cfg['keyspace']} "
            "WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}"
        )
    session.set_keyspace(cfg["keyspace"])
    return cluster, session
