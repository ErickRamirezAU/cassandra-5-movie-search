#!/usr/bin/env python3
"""
Loader for the cassandra-5-movie-search cMovie dataset.

Selects 1,000 films stratified by decade (250 per decade, configurable),
resolves each into a full `movies` row using film data from Wikidata and
plot/cast data from Wikipedia, and loads it into Astra DB or a plain
Apache Cassandra cluster. Then, as stage 2, fills each row's `wikipedia_url`
and `plot_embedding` (a `vector<float, 3072>` from Google's
`gemini-embedding-2`, which needs GEMINI_API_KEY in .env).

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
- Connects before selecting films, and inserts them in batches of 25
  (--batch-size) as they're accepted, so a run that fails partway keeps
  every film it had already inserted.
- Resumes by default: a film already in the `movies` table counts as
  accepted without being fetched again, so re-running after a failure picks
  up where the last run stopped. Pass --no-resume to rebuild every film.
- Stage 2 only touches rows that are missing a value, so it resumes too:
  re-running after Google's free tier limit stops it carries on with the
  movies that have no embedding yet. It sends 100 plots per request and
  waits whenever Google asks it to. Google's free tier counts every plot,
  not just every request, so a full load of 1,000 movies can use up a whole
  day's allowance. Use --max-embeddings to cap a run.
- `--backfill` skips stage 1 and runs stage 2 only, on the movies already in
  your table, for readers who loaded them in week 1 or 2. Run the week 3
  `ALTER TABLE` first.

Requires cassandra-driver (PyPI wheels cover CPython 3.10-3.14).
Run from the repo's root directory, in a virtual environment:

    ./.venv/bin/python tools/loader.py --dry-run --films-per-decade 5

Drop --dry-run for a real load:

    ./.venv/bin/python tools/loader.py --films-per-decade 250

Add the week 3 columns to an existing table, then fill them:

    ./.venv/bin/python tools/loader.py --backfill
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

from embeddings import (  # noqa: E402
    BATCH_SIZE as EMBED_BATCH_SIZE,
    DailyLimitReached,
    EmbeddingError,
    embed_texts,
)
from probe_wikidata_wikipedia import (  # noqa: E402
    DECADES,
    enrich_candidates,
    fetch_decade_candidates,
    fetch_infobox_wikitext,
    fetch_pub_dates,
    parse_starring_names,
    resolve_release_year,
    resolve_runtime,
    sparql_query,
    wikipedia_api,
)
from connection import (  # noqa: E402
    DEFAULT_ENV_PATH,
    DEFAULT_SCB_PATH,
    build_config,
    connect,
    load_env,
)

FILMS_PER_DECADE_DEFAULT = 250
CANDIDATES_PER_DECADE_DEFAULT = 400  # default candidate pool size per decade
SITELINKS_THRESHOLD_DEFAULT = 15
PROGRESS_INTERVAL_DEFAULT = 60
BATCH_SIZE_DEFAULT = 25
SITELINKS_BATCH_SIZE = 200
# Seconds from the start of one embedding request to the start of the next,
# so a full load stays near 100 plots a minute.
EMBED_SECONDS_PER_BATCH = 60

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
    # Films already in the table from an earlier run, kept rather than
    # fetched again. They count towards the decade's target.
    resumed_qids: list[str] = field(default_factory=list)
    skipped_qids: list[str] = field(default_factory=list)
    candidates_pulled: int = 0
    true_decade_verified: int = 0

    @property
    def filled(self) -> int:
        return len(self.accepted) + len(self.resumed_qids)


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
    already_loaded: set[str] | None = None,
    insert_batch: Callable[[list[MovieRecord]], None] | None = None,
    batch_size: int = BATCH_SIZE_DEFAULT,
) -> DecadeResult:
    result = DecadeResult(name=name)
    already_loaded = already_loaded or set()
    pending: list[MovieRecord] = []

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
            _print_heartbeat(name, target, walked, len(verified), result.filled, len(result.skipped_qids), time.monotonic() - start)

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        for qid, release_year in verified:
            walked += 1
            if result.filled >= target:
                break
            # Walking in rank order and counting a loaded film as accepted
            # keeps the same selection a single uninterrupted run would make.
            if qid in already_loaded:
                result.resumed_qids.append(qid)
                continue
            record = build_record(qid, release_year, enrichment.get(qid, {"runtimes": [], "genres": [], "enwiki_title": None}))
            if record is None:
                result.skipped_qids.append(qid)
                continue
            result.accepted.append(record)
            if result.filled % 25 == 0:
                print(f"  ...{result.filled}/{target} accepted ({len(result.skipped_qids)} skipped so far)", flush=True)
            if insert_batch is not None:
                pending.append(record)
                if len(pending) >= batch_size:
                    insert_batch(pending)
                    pending = []
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join()

    if insert_batch is not None and pending:
        insert_batch(pending)

    if result.filled < target:
        print(
            f"  ** only {result.filled}/{target} filled for {name} -- "
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
    title_words set<text>,
    plot_embedding vector<float, 3072>,
    wikipedia_url text
)
"""

INSERT_MOVIE = """
INSERT INTO movies (
    movie_id, title, release_year, genres, runtime,
    cmovie_rating, cmovie_votes, cmovie_popularity,
    plot, actors, actor_words, title_words
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class MovieInserter:
    """Creates the `movies` table, then inserts records a batch at a time,
    keeping running totals for the summary at the end of the run."""

    def __init__(self, session) -> None:
        self.session = session
        session.execute(CREATE_MOVIES_TABLE)
        self.insert_stmt = session.prepare(INSERT_MOVIE)
        self.inserted = 0
        self.failed = 0

    def loaded_movie_ids(self) -> set[str]:
        return {row.movie_id for row in self.session.execute("SELECT movie_id FROM movies")}

    def insert_batch(self, records: list[MovieRecord]) -> None:
        from cassandra.concurrent import execute_concurrent_with_args

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
        results = execute_concurrent_with_args(self.session, self.insert_stmt, params, concurrency=50, raise_on_first_error=False)
        for (success, exc_or_result), record in zip(results, records):
            if success:
                self.inserted += 1
            else:
                self.failed += 1
                print(f"  ** insert failed for {record.movie_id} ({record.title}): {exc_or_result}", file=sys.stderr)
        print(f"  ...inserted {len(records)} row(s), {self.inserted} this run", flush=True)

    def print_summary(self) -> None:
        total = self.inserted + self.failed
        if self.failed:
            print(f"  {self.failed}/{total} inserts failed", file=sys.stderr)
        else:
            print(f"  all {total} rows inserted")



# ---------------------------------------------------------------------------
# Stage 2: wikipedia_url and plot_embedding
# ---------------------------------------------------------------------------

STAGE2_COLUMNS_HELP = (
    "The movies table doesn't have the week 3 columns yet. In the Astra CQL "
    "console (or cqlsh), run:\n\n"
    "    ALTER TABLE movies ADD (plot_embedding vector<float, 3072>, wikipedia_url text);\n\n"
    "then run this command again."
)


def fetch_wikipedia_urls(qids: list[str]) -> dict[str, str]:
    """English Wikipedia article URLs for Wikidata IDs, 200 per query."""
    urls: dict[str, str] = {}
    for i in range(0, len(qids), SITELINKS_BATCH_SIZE):
        values = " ".join(f"wd:{qid}" for qid in qids[i : i + SITELINKS_BATCH_SIZE])
        query = (
            f"SELECT ?item ?article WHERE {{ VALUES ?item {{ {values} }} "
            "?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }"
        )
        for binding in sparql_query(query):
            urls[binding["item"]["value"].rsplit("/", 1)[1]] = binding["article"]["value"]
    return urls


def run_stage_two(session, api_key: str | None, max_embeddings: int | None) -> None:
    """Fill wikipedia_url and plot_embedding for every row missing them."""
    from cassandra import InvalidRequest
    from cassandra.concurrent import execute_concurrent_with_args

    try:
        # The default 10 second client timeout is too short once most rows hold
        # a 3,072 float vector (about 12 KB each), so give the full scan longer.
        rows = list(session.execute("SELECT movie_id, plot, plot_embedding, wikipedia_url FROM movies", timeout=120))
    except InvalidRequest:
        sys.exit(STAGE2_COLUMNS_HELP)

    print("\n=== Stage 2: wikipedia_url and plot_embedding ===")

    # Wikipedia article URLs. One SPARQL query per 200 movies, no Wikipedia
    # requests, so this is quick and needs no Gemini key.
    missing_urls = [r.movie_id for r in rows if not r.wikipedia_url]
    if missing_urls:
        print(f"  looking up {len(missing_urls)} Wikipedia article URL(s)...")
        urls = fetch_wikipedia_urls(missing_urls)
        update_url = session.prepare("UPDATE movies SET wikipedia_url = ? WHERE movie_id = ?")
        execute_concurrent_with_args(session, update_url, [(url, qid) for qid, url in urls.items()], concurrency=50)
        print(f"  {len(urls)} of {len(missing_urls)} URL(s) saved")
    else:
        print("  every movie already has a wikipedia_url")

    todo = [r for r in rows if r.plot and r.plot_embedding is None]
    if not todo:
        print("  every movie already has a plot_embedding")
        return
    if not api_key:
        print(
            f"  {len(todo)} movie(s) have no plot_embedding. Set GEMINI_API_KEY in .env and run "
            "`python tools/loader.py --backfill` to embed them.",
            file=sys.stderr,
        )
        return
    if max_embeddings is not None:
        todo = todo[:max_embeddings]

    update_embedding = session.prepare("UPDATE movies SET plot_embedding = ? WHERE movie_id = ?")
    done = 0
    print(f"  embedding {len(todo)} plot(s), {EMBED_BATCH_SIZE} per request...")
    for i in range(0, len(todo), EMBED_BATCH_SIZE):
        batch = todo[i : i + EMBED_BATCH_SIZE]
        started = time.monotonic()
        try:
            vectors = embed_texts(api_key, [r.plot for r in batch])
        except DailyLimitReached as e:
            print(
                f"\n  Google's free tier limit for today is used up: {e}\n"
                f"  {done} of {len(todo)} plot(s) were embedded and saved. "
                "Run the same command again later and it carries on with the rest.",
                file=sys.stderr,
            )
            return
        except EmbeddingError as e:
            print(f"\n  {e}\n  {done} plot(s) were embedded and saved. Run the same command again to carry on.", file=sys.stderr)
            return
        execute_concurrent_with_args(session, update_embedding, [(v, r.movie_id) for v, r in zip(vectors, batch)], concurrency=20)
        done += len(batch)
        print(f"  ...embedded {done}/{len(todo)}", flush=True)
        remaining = EMBED_SECONDS_PER_BATCH - (time.monotonic() - started)
        if i + EMBED_BATCH_SIZE < len(todo) and remaining > 0:
            time.sleep(remaining)
    print(f"  done: {done} plot(s) embedded")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # Print each line as it happens even when output is redirected to a
    # file, where Python would otherwise hold it in a buffer, so a log of a
    # long run can be followed live.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--films-per-decade", type=int, default=FILMS_PER_DECADE_DEFAULT)
    parser.add_argument("--candidates-per-decade", type=int, default=CANDIDATES_PER_DECADE_DEFAULT)
    parser.add_argument("--sitelinks-threshold", type=int, default=SITELINKS_THRESHOLD_DEFAULT)
    parser.add_argument("--progress-interval", type=int, default=PROGRESS_INTERVAL_DEFAULT, help="Seconds between progress updates while enriching candidates (default: 60)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT, help="Insert accepted films in batches of this many (default: 25)")
    parser.add_argument("--no-resume", action="store_true", help="Fetch and insert every film again, including films already in the table")
    parser.add_argument("--decade", action="append", choices=[d[0] for d in DECADES], help="Restrict to one or more decades (repeatable). Default: all four")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--scb", default=None, help=f"Path to the Secure Connect Bundle (default: {DEFAULT_SCB_PATH})")
    parser.add_argument("--keyspace", default=None, help="Override ASTRA_DB_KEYSPACE / CASSANDRA_KEYSPACE")
    parser.add_argument("--backfill", action="store_true", help="Skip stage 1 and only fill wikipedia_url and plot_embedding for the movies already in your table")
    parser.add_argument("--no-embeddings", action="store_true", help="Skip stage 2")
    parser.add_argument("--max-embeddings", type=int, default=None, help="Embed at most this many plots in this run (default: all that are missing)")
    parser.add_argument("--dry-run", action="store_true", help="Select and build records but don't connect to Astra or write anything")
    parser.add_argument("--out-json", default=None, help="Also write the built records to this JSON file")
    args = parser.parse_args()

    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if args.backfill and (args.dry_run or args.no_embeddings):
        parser.error("--backfill can't be combined with --dry-run or --no-embeddings")
    if args.max_embeddings is not None and args.max_embeddings < 1:
        parser.error("--max-embeddings must be at least 1")

    # Connect before the (potentially multi-minute, network-bound) film
    # selection below, so a missing or wrong token or bundle fails straight
    # away, and so films can be inserted in batches as they're accepted.
    # Skipped for --dry-run, which is meant to work with no DB config at all.
    cluster = None
    inserter = None
    already_loaded: set[str] = set()
    if not args.dry_run:
        cfg = build_config(args.env_file, args.scb, args.keyspace)
        if cfg["backend"] == "astra":
            print(f"Connecting to Astra keyspace '{cfg['keyspace']}' via {cfg['scb_path'].name}...")
        else:
            host_list = ", ".join(f"{h}:{p}" for h, p in cfg["hosts"])
            print(f"Connecting to keyspace '{cfg['keyspace']}' on {host_list}...")
        cluster, session = connect(cfg, create_keyspace=True)
        if args.backfill:
            try:
                run_stage_two(session, load_env(Path(args.env_file)).get("GEMINI_API_KEY"), args.max_embeddings)
            finally:
                cluster.shutdown()
            return
        inserter = MovieInserter(session)
        if not args.no_resume:
            already_loaded = inserter.loaded_movie_ids()
            if already_loaded:
                print(f"{len(already_loaded)} film(s) already in the table will be kept, not fetched again (--no-resume to rebuild them)")

    decades = [d for d in DECADES if not args.decade or d[0] in args.decade]

    all_records: list[MovieRecord] = []
    all_resumed = 0
    all_skipped: dict[str, list[str]] = {}

    try:
        for name, start_year, end_year in decades:
            print(f"\n=== {name} ({start_year}-{end_year}) ===")
            result = select_decade(
                name, start_year, end_year,
                args.films_per_decade, args.candidates_per_decade, args.sitelinks_threshold,
                args.progress_interval,
                already_loaded,
                inserter.insert_batch if inserter else None,
                args.batch_size,
            )
            all_records.extend(result.accepted)
            all_resumed += len(result.resumed_qids)
            all_skipped[name] = result.skipped_qids
            print(
                f"  {name}: {len(result.accepted)} accepted, {len(result.resumed_qids)} already loaded, "
                f"{len(result.skipped_qids)} skipped for a missing field"
            )
    except BaseException:
        if cluster is not None:
            cluster.shutdown()
        raise

    print(f"\nTotal films built: {len(all_records)}")
    if all_resumed:
        print(f"Total kept from an earlier run: {all_resumed}")
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

    try:
        if not all_records and not all_resumed:
            sys.exit("No records built, nothing to load.")

        inserter.print_summary()

        if not args.no_embeddings:
            run_stage_two(session, load_env(Path(args.env_file)).get("GEMINI_API_KEY"), args.max_embeddings)
    finally:
        cluster.shutdown()

if __name__ == "__main__":
    main()
