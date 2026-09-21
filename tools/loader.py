#!/usr/bin/env python3
"""
Loader for the cassandra-5-movie-search cMovie dataset.

Selects 1,000 films stratified by decade (250 per decade, configurable),
resolves each into a full `movies` row using film data from Wikidata and
plot/cast data from Wikipedia, and loads it into Astra DB or a plain
Apache Cassandra cluster. Does not generate embeddings or touch a
`vector<float, 3072>` column.

Selection and enrichment (the Wikidata/Wikipedia queries and retries) come
from probe_wikidata_wikipedia.py in this directory. On top of that, this
script:

- Skips a candidate outright if it's missing runtime, plot or `starring`,
  and pulls the next-ranked candidate from the same decade's pool instead.
- Generates `cmovie_rating`, `cmovie_votes` and `cmovie_popularity`, seeded
  from each film's Wikidata ID so every reader loading the same film gets
  the same numbers.
- Tokenises `title_words` / `actor_words`: colons, commas, dashes (ASCII
  and en/em dash) and periods are removed; apostrophes are kept.
- Trims the trailing " film" suffix from Wikidata genre labels ("science
  fiction film" -> "science fiction").
- Strips the Wikipedia disambiguation suffix ("Dune (2021 film)") from the
  stored `title`, while still using the exact Wikipedia page title for
  API calls.
- Extracts and cleans the Plot section text from the Wikipedia page.
- Loads into Astra DB via a Secure Connect Bundle, or into a plain
  Cassandra cluster (set CASSANDRA_HOSTS in .env) if no Astra token is
  configured. Either backend defaults its keyspace to "default_keyspace"
  if one isn't set.

Requires cassandra-driver (PyPI wheels cover CPython 3.10-3.14).
Run from the repo's root directory, in a virtual environment:

    ./.venv/bin/python tools/loader.py --dry-run --films-per-decade 5

Drop --dry-run for a real load:

    ./.venv/bin/python tools/loader.py --films-per-decade 250
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

from probe_wikidata_wikipedia import (  # noqa: E402
    DECADES,
    enrich_candidates,
    fetch_decade_candidates,
    fetch_infobox_wikitext,
    fetch_pub_dates,
    parse_starring_names,
    resolve_release_year,
    resolve_runtime,
    wikipedia_api,
)

DEFAULT_ENV_PATH = PROJECT_DIR / ".env"
DEFAULT_SCB_PATH = PROJECT_DIR / "secure-connect-cmovies.zip"

FILMS_PER_DECADE_DEFAULT = 250
CANDIDATES_PER_DECADE_DEFAULT = 400  # default candidate pool size per decade
SITELINKS_THRESHOLD_DEFAULT = 15
PROGRESS_INTERVAL_DEFAULT = 60

# Deleted outright, not replaced with a space. Includes the en/em dash
# Wikipedia titles use ("John Wick: Chapter 3 – Parabellum"), not just the
# ASCII hyphen-minus in compound names.
TOKEN_STRIP_RE = re.compile(r"[:,.\-–—]")

GENRE_FILM_SUFFIX_RE = re.compile(r"\s+film$", re.IGNORECASE)

# Matches a trailing Wikipedia disambiguator such as "(2021 film)",
# "(1994 South Korean film)" or "(film)".
TITLE_DISAMBIGUATOR_RE = re.compile(r"\s*\([^()]*\bfilms?\b[^()]*\)\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Text cleanup
# ---------------------------------------------------------------------------


def tokenise(text: str) -> list[str]:
    stripped = TOKEN_STRIP_RE.sub("", text)
    return [t.lower() for t in stripped.split() if t]


def trim_genre_label(label: str) -> str:
    trimmed = GENRE_FILM_SUFFIX_RE.sub("", label).strip()
    return trimmed or label


def strip_disambiguator(page_title: str) -> str:
    return TITLE_DISAMBIGUATOR_RE.sub("", page_title).strip()


_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
_REF_RE = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_RE = re.compile(r"^==+\s*.*?\s*==+\s*")
_WIKILINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]")
_BOLD_ITALIC_RE = re.compile(r"'{2,5}")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def clean_plot_wikitext(wikitext: str) -> str:
    """Best-effort wikitext -> plain text for one section's body.

    Not a full wikitext parser -- strips refs, templates (non-nested, run
    a few times to catch shallow nesting), the section's own heading line,
    wikilinks (kept as display text), bold/italic markup and stray HTML,
    then collapses whitespace. Good enough for embedding input and app
    display; not guaranteed to be spotless on every page.
    """
    text = _HEADING_RE.sub("", wikitext, count=1)
    text = _REF_RE.sub("", text)
    text = _COMMENT_RE.sub("", text)
    for _ in range(3):
        # A space, not "" -- templates like {{snd}} (a spaced dash) sit
        # between two words with no literal space in the wikitext, so
        # deleting to nothing merges them ("three{{snd}}Miller" ->
        # "threeMiller"). The whitespace collapse below cleans up any
        # doubled spaces this introduces elsewhere.
        text = _TEMPLATE_RE.sub(" ", text)
    text = _WIKILINK_RE.sub(r"\1", text)
    text = _BOLD_ITALIC_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Plot section fetch -- the probe only checked a Plot section *exists*
# (has_plot_section); this loader needs the actual text.
# ---------------------------------------------------------------------------


def fetch_sections(title: str) -> list[dict]:
    data = wikipedia_api({"action": "parse", "page": title, "prop": "sections"})
    if "error" in data:
        return []
    return data.get("parse", {}).get("sections", [])


def find_plot_section_index(sections: list[dict]) -> str | None:
    for s in sections:
        if s["line"].strip().lower() == "plot":
            return s["index"]
    return None


def fetch_plot_text(title: str) -> str | None:
    sections = fetch_sections(title)
    index = find_plot_section_index(sections)
    if index is None:
        return None
    data = wikipedia_api({"action": "parse", "page": title, "prop": "wikitext", "section": str(index)})
    if "error" in data:
        return None
    wikitext = data.get("parse", {}).get("wikitext", {}).get("*")
    if not wikitext:
        return None
    cleaned = clean_plot_wikitext(wikitext)
    return cleaned or None


# ---------------------------------------------------------------------------
# Generated cMovie metrics -- seeded from the QID so every reader loading
# the same film gets identical numbers (locked decision).
# ---------------------------------------------------------------------------


def generate_cmovie_metrics(qid: str) -> tuple[float, int, float]:
    seed = int(hashlib.sha256(qid.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    rating = round(max(0.0, min(10.0, rng.gauss(6.5, 1.3))), 1)
    votes = max(10, int(rng.lognormvariate(8.5, 1.8)))
    popularity = round(rng.lognormvariate(2.0, 1.5), 2)
    return rating, votes, popularity


# ---------------------------------------------------------------------------
# Film record
# ---------------------------------------------------------------------------


@dataclass
class MovieRecord:
    movie_id: str
    title: str
    release_year: int
    genres: list[str]
    runtime: int
    cmovie_rating: float
    cmovie_votes: int
    cmovie_popularity: float
    plot: str
    actors: list[str]
    actor_words: list[str]
    title_words: list[str]


@dataclass
class DecadeResult:
    name: str
    accepted: list[MovieRecord] = field(default_factory=list)
    skipped_qids: list[str] = field(default_factory=list)
    candidates_pulled: int = 0
    true_decade_verified: int = 0


def build_record(qid: str, release_year: int, enrichment: dict) -> MovieRecord | None:
    runtime_minutes, _ambiguous = resolve_runtime(enrichment.get("runtimes", []))
    if runtime_minutes is None:
        return None

    genres = [trim_genre_label(g) for g in enrichment.get("genres", [])]
    if not genres:
        return None

    page_title = enrichment.get("enwiki_title")
    if not page_title:
        return None

    plot = fetch_plot_text(page_title)
    if not plot:
        return None

    infobox = fetch_infobox_wikitext(page_title)
    starring = parse_starring_names(infobox) if infobox else []
    if not starring:
        return None

    display_title = strip_disambiguator(page_title)
    actors = [s["name"] for s in starring]

    actor_words: set[str] = set()
    for name in actors:
        actor_words.update(tokenise(name))
    title_words = set(tokenise(display_title))

    rating, votes, popularity = generate_cmovie_metrics(qid)

    return MovieRecord(
        movie_id=qid,
        title=display_title,
        release_year=release_year,
        genres=genres,
        runtime=int(round(runtime_minutes)),
        cmovie_rating=rating,
        cmovie_votes=votes,
        cmovie_popularity=popularity,
        plot=plot,
        actors=actors,
        actor_words=sorted(actor_words),
        title_words=sorted(title_words),
    )


def _print_heartbeat(name: str, target: int, walked: int, total: int, accepted: int, skipped: int, elapsed: float) -> None:
    print(
        f"  ...[{elapsed:.0f}s] {name}: walked {walked}/{total} candidates, "
        f"{accepted}/{target} accepted, {skipped} skipped",
        flush=True,
    )


def select_decade(
    name: str,
    start_year: int,
    end_year: int,
    target: int,
    pool_size: int,
    sitelinks_threshold: int,
    progress_interval: int,
) -> DecadeResult:
    result = DecadeResult(name=name)

    print(f"  fetching up to {pool_size} candidates (sitelinks >= {sitelinks_threshold})...")
    candidates = fetch_decade_candidates(start_year, end_year, pool_size, sitelinks_threshold)
    result.candidates_pulled = len(candidates)

    print(f"  verifying true earliest release year for {len(candidates)} candidate(s)...")
    pub_dates = fetch_pub_dates([c["qid"] for c in candidates])
    verified = []
    for c in candidates:
        release_year = resolve_release_year(pub_dates.get(c["qid"], []))
        if release_year is not None and start_year <= release_year <= end_year:
            verified.append((c["qid"], release_year))
    result.true_decade_verified = len(verified)
    print(f"  {len(verified)} of {len(candidates)} confirmed true {name} release")

    print(f"  enriching {len(verified)} candidate(s) with runtime, genre, enwiki title...")
    enrichment = enrich_candidates([qid for qid, _year in verified])

    # A background timer, not an inline elapsed-time check in the loop below,
    # so a heartbeat still fires on schedule even if a single Wikimedia
    # request in build_record() hangs rather than erroring out quickly.
    walked = 0
    start = time.monotonic()
    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        while not stop_heartbeat.wait(progress_interval):
            _print_heartbeat(name, target, walked, len(verified), len(result.accepted), len(result.skipped_qids), time.monotonic() - start)

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        for qid, release_year in verified:
            walked += 1
            if len(result.accepted) >= target:
                break
            record = build_record(qid, release_year, enrichment.get(qid, {"runtimes": [], "genres": [], "enwiki_title": None}))
            if record is None:
                result.skipped_qids.append(qid)
                continue
            result.accepted.append(record)
            if len(result.accepted) % 25 == 0:
                print(f"  ...{len(result.accepted)}/{target} accepted ({len(result.skipped_qids)} skipped so far)", flush=True)
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join()

    if len(result.accepted) < target:
        print(
            f"  ** only {len(result.accepted)}/{target} filled for {name} -- "
            f"{len(verified)} true-decade candidates were not enough spares. "
            f"Re-run with a larger --candidates-per-decade for this decade.",
            file=sys.stderr,
        )

    return result


# ---------------------------------------------------------------------------
# Astra DB load
# ---------------------------------------------------------------------------

CREATE_MOVIES_TABLE = """
CREATE TABLE IF NOT EXISTS movies (
    movie_id text PRIMARY KEY,
    title text,
    release_year int,
    genres set<text>,
    runtime int,
    cmovie_rating float,
    cmovie_votes int,
    cmovie_popularity float,
    plot text,
    actors list<text>,
    actor_words set<text>,
    title_words set<text>
)
"""

INSERT_MOVIE = """
INSERT INTO movies (
    movie_id, title, release_year, genres, runtime,
    cmovie_rating, cmovie_votes, cmovie_popularity,
    plot, actors, actor_words, title_words
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


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


KEYSPACE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def build_config(args) -> dict:
    env_path = Path(args.env_file)
    env = load_env(env_path)

    if env.get("ASTRA_DB_TOKEN"):
        scb_path = Path(args.scb or DEFAULT_SCB_PATH)
        if not scb_path.exists():
            sys.exit(
                f"Secure Connect Bundle not found at {scb_path}. Download it from your "
                f"Astra DB dashboard and save it in the project's root directory "
                f"({PROJECT_DIR}) as {DEFAULT_SCB_PATH.name} -- or, if your database isn't "
                f"named '{DEFAULT_SCB_PATH.stem.removeprefix('secure-connect-')}', pass its "
                f"bundle's path with --scb /path/to/your-bundle.zip."
            )
        return {
            "backend": "astra",
            "token": env["ASTRA_DB_TOKEN"],
            "keyspace": args.keyspace or env.get("ASTRA_DB_KEYSPACE") or "default_keyspace",
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
        sys.exit(f"CASSANDRA_HOSTS in {args.env_file} is empty")

    username = env.get("CASSANDRA_USERNAME") or None
    password = env.get("CASSANDRA_PASSWORD") or None
    if bool(username) != bool(password):
        sys.exit("Set both CASSANDRA_USERNAME and CASSANDRA_PASSWORD, or leave both empty for an unauthenticated cluster")

    keyspace = args.keyspace or env.get("CASSANDRA_KEYSPACE") or "default_keyspace"
    if not KEYSPACE_NAME_RE.match(keyspace):
        sys.exit(f"'{keyspace}' isn't a valid keyspace name")

    return {
        "backend": "cassandra",
        "hosts": hosts,
        "username": username,
        "password": password,
        "keyspace": keyspace,
    }


def connect(cfg: dict):
    from cassandra import ConsistencyLevel
    from cassandra.auth import PlainTextAuthProvider
    from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile

    # Astra rejects ANY/ONE/LOCAL_ONE for writes outright ("Provided value
    # ONE is not allowed for Write Consistency Level"); LOCAL_QUORUM is a
    # sane default write consistency on any Cassandra cluster, Astra or open
    # source, so it's used here regardless of backend. Set via an execution
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
    session.execute(
        f"CREATE KEYSPACE IF NOT EXISTS {cfg['keyspace']} "
        "WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}"
    )
    session.set_keyspace(cfg["keyspace"])
    return cluster, session


def load_records(session, records: list[MovieRecord]) -> None:
    from cassandra.concurrent import execute_concurrent_with_args

    session.execute(CREATE_MOVIES_TABLE)
    insert_stmt = session.prepare(INSERT_MOVIE)

    params = [
        (
            r.movie_id,
            r.title,
            r.release_year,
            r.genres,
            r.runtime,
            r.cmovie_rating,
            r.cmovie_votes,
            r.cmovie_popularity,
            r.plot,
            r.actors,
            r.actor_words,
            r.title_words,
        )
        for r in records
    ]
    results = execute_concurrent_with_args(session, insert_stmt, params, concurrency=50, raise_on_first_error=False)
    failed = 0
    for (success, exc_or_result), record in zip(results, records):
        if not success:
            failed += 1
            print(f"  ** insert failed for {record.movie_id} ({record.title}): {exc_or_result}", file=sys.stderr)
    if failed:
        print(f"  {failed}/{len(records)} inserts failed", file=sys.stderr)
    else:
        print(f"  all {len(records)} rows inserted")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--films-per-decade", type=int, default=FILMS_PER_DECADE_DEFAULT)
    parser.add_argument("--candidates-per-decade", type=int, default=CANDIDATES_PER_DECADE_DEFAULT)
    parser.add_argument("--sitelinks-threshold", type=int, default=SITELINKS_THRESHOLD_DEFAULT)
    parser.add_argument("--progress-interval", type=int, default=PROGRESS_INTERVAL_DEFAULT, help="Seconds between progress updates while enriching candidates (default: 60)")
    parser.add_argument("--decade", action="append", choices=[d[0] for d in DECADES], help="Restrict to one or more decades (repeatable). Default: all four")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--scb", default=None, help=f"Path to the Secure Connect Bundle (default: {DEFAULT_SCB_PATH})")
    parser.add_argument("--keyspace", default=None, help="Override ASTRA_DB_KEYSPACE / CASSANDRA_KEYSPACE")
    parser.add_argument("--dry-run", action="store_true", help="Select and build records but don't connect to Astra or write anything")
    parser.add_argument("--out-json", default=None, help="Also write the built records to this JSON file")
    args = parser.parse_args()

    # Fail fast on a missing/misconfigured backend, before the (potentially
    # multi-minute, network-bound) film selection below runs for nothing.
    # Skipped for --dry-run, which is meant to work with no DB config at all.
    cfg = None if args.dry_run else build_config(args)

    decades = [d for d in DECADES if not args.decade or d[0] in args.decade]

    all_records: list[MovieRecord] = []
    all_skipped: dict[str, list[str]] = {}

    for name, start_year, end_year in decades:
        print(f"\n=== {name} ({start_year}-{end_year}) ===")
        result = select_decade(
            name, start_year, end_year,
            args.films_per_decade, args.candidates_per_decade, args.sitelinks_threshold,
            args.progress_interval,
        )
        all_records.extend(result.accepted)
        all_skipped[name] = result.skipped_qids
        print(f"  {name}: {len(result.accepted)} accepted, {len(result.skipped_qids)} skipped for a missing field")

    print(f"\nTotal films built: {len(all_records)}")
    total_skipped = sum(len(v) for v in all_skipped.values())
    if total_skipped:
        print(f"Total skipped for a missing required field (runtime/plot/starring): {total_skipped}")

    if args.out_json:
        Path(args.out_json).write_text(json.dumps([asdict(r) for r in all_records], indent=2))
        print(f"Wrote {args.out_json}")

    if args.dry_run:
        print("\n--dry-run set: not connecting to Astra. Sample record:")
        if all_records:
            print(json.dumps(asdict(all_records[0]), indent=2)[:2000])
        return

    if not all_records:
        sys.exit("No records built, nothing to load.")

    if cfg["backend"] == "astra":
        print(f"\nConnecting to Astra keyspace '{cfg['keyspace']}' via {cfg['scb_path'].name}...")
    else:
        host_list = ", ".join(f"{h}:{p}" for h, p in cfg["hosts"])
        print(f"\nConnecting to keyspace '{cfg['keyspace']}' on {host_list}...")
    cluster, session = connect(cfg)
    try:
        load_records(session, all_records)
    finally:
        cluster.shutdown()


if __name__ == "__main__":
    main()
