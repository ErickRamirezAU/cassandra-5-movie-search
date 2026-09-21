#!/usr/bin/env python3
"""
Coverage report and film data helper for the cassandra-5-movie-search loader.

Reports, per decade (1990s-2020s), how many candidate films have a release
year, a runtime, at least one genre and an English Wikipedia plot section,
and how many also have a Wikipedia infobox `starring` list. It writes to no
database and calls no API that needs a key. `loader.py` imports the film
selection and enrichment functions from this module.

Standard library only, with no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERIES_REPO_URL = "https://github.com/ErickRamirezAU/cassandra-5-movie-search"
USER_AGENT = f"cassandra-5-movie-search-probe/0.1 ({SERIES_REPO_URL})"

WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIPEDIA_API_ENDPOINT = "https://en.wikipedia.org/w/api.php"

DECADES = [
    ("1990s", 1990, 1999),
    ("2000s", 2000, 2009),
    ("2010s", 2010, 2019),
    ("2020s", 2020, 2029),
]

FILMS_PER_DECADE_TARGET = 250

# Runtime values (minutes) outside this band are treated as unresolved
# rather than silently unit-converted, because Wikidata's runtime property
# (P2047) is sometimes stored in seconds.
PLAUSIBLE_RUNTIME_MINUTES = (40, 400)

REQUEST_SLEEP_SECONDS = 0.3
MAX_RETRIES = 3

STARRING_FIELD_RE = re.compile(r"\|\s*starring\s*=(.*?)\n\|", re.S | re.I)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
BULLET_RE = re.compile(r"^\*\s*(.+)$", re.M)


# ---------------------------------------------------------------------------
# HTTP helpers -- serial requests only, per Wikimedia's API etiquette and
# the Wikidata Query Service limits.
# ---------------------------------------------------------------------------


def _get(url: str, params: dict, accept: str) -> dict:
    query_string = urllib.parse.urlencode(params)
    full_url = f"{url}?{query_string}"
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(full_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "5"))
                print(f"  [429] rate limited, waiting {wait}s (attempt {attempt})", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code in (502, 503, 504):
                wait = 3 * attempt
                print(f"  [{e.code}] transient error, waiting {wait}s (attempt {attempt})", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"Gave up after {MAX_RETRIES} attempts: {url}")


def sparql_query(query: str) -> list[dict]:
    data = _get(
        WIKIDATA_SPARQL_ENDPOINT,
        {"query": query, "format": "json"},
        "application/sparql-results+json",
    )
    time.sleep(REQUEST_SLEEP_SECONDS)
    return data["results"]["bindings"]


def wikipedia_api(params: dict) -> dict:
    full_params = {**params, "format": "json", "maxlag": "5"}
    data = _get(WIKIPEDIA_API_ENDPOINT, full_params, "application/json")
    time.sleep(REQUEST_SLEEP_SECONDS)
    return data


# ---------------------------------------------------------------------------
# Phase 1: rank candidates per decade by sitelink count.
#
# Sitelinks are filtered before the P577 join, not after, to keep the
# query cheap: an unfiltered join across every film's full publication
# date history is too expensive for the query service and times out. Films
# below the threshold can never make the top slice anyway once thousands of
# high-sitelink films exist per decade, so the early filter costs nothing.
#
# The earliest P577 year stands in for "release year" because P577 can
# carry more than one date per film (country premieres, anniversary
# re-releases). Taking the minimum recovers the original release year
# instead of a re-release year.
# ---------------------------------------------------------------------------


def fetch_decade_candidates(start_year: int, end_year: int, limit: int, sitelinks_threshold: int) -> list[dict]:
    # Deliberately no GROUP BY/HAVING here: an aggregate over every P577
    # value for every film above the sitelinks threshold is too expensive
    # for the query service. A plain FILTER on YEAR(?pubDate) is cheap
    # because it doesn't need to see a film's full date history, but it
    # means a film can appear in the wrong decade's candidate list purely
    # because it has a publication date (any country premiere or
    # re-release) inside this decade's range. Candidates are ranked by
    # sitelinks, so a handful of blockbusters with a stray re-release date
    # can crowd real candidates out of this decade's top slice.
    # verify_true_decade() below re-derives the true earliest release year
    # for the whole pool and discards those films before anything is
    # sliced to --film-limit, so a real film ranked below one of them still
    # gets its turn.
    query = f"""
    SELECT DISTINCT ?film ?sitelinks WHERE {{
      ?film wdt:P31 wd:Q11424;
            wikibase:sitelinks ?sitelinks;
            wdt:P577 ?pubDate.
      FILTER(?sitelinks >= {sitelinks_threshold})
      FILTER(YEAR(?pubDate) >= {start_year} && YEAR(?pubDate) <= {end_year})
    }}
    ORDER BY DESC(?sitelinks)
    LIMIT {limit}
    """
    rows = sparql_query(query)
    candidates = []
    for row in rows:
        qid = row["film"]["value"].rsplit("/", 1)[-1]
        candidates.append(
            {
                "qid": qid,
                "sitelinks": int(row["sitelinks"]["value"]),
            }
        )
    return candidates


# ---------------------------------------------------------------------------
# Phase 1b: re-derive each Phase A candidate's true earliest P577 year,
# bounded via VALUES to just that decade's candidate pool, which keeps it
# cheap. Run over the whole candidate pool, before any --film-limit
# slicing, so a real film ranked below a wrongly dated blockbuster still
# gets kept.
# ---------------------------------------------------------------------------


def fetch_pub_dates(qids: list[str], batch_size: int = 50) -> dict[str, list[str]]:
    pub_dates_by_qid: dict[str, list[str]] = {}
    for i in range(0, len(qids), batch_size):
        batch = qids[i : i + batch_size]
        values = " ".join(f"wd:{q}" for q in batch)
        query = f"""
        SELECT ?film (GROUP_CONCAT(DISTINCT ?pubDate; separator="|") AS ?pubDates) WHERE {{
          VALUES ?film {{ {values} }}
          OPTIONAL {{ ?film wdt:P577 ?pubDate. }}
        }}
        GROUP BY ?film
        """
        rows = sparql_query(query)
        for row in rows:
            qid = row["film"]["value"].rsplit("/", 1)[-1]
            dates = row["pubDates"]["value"].split("|") if row.get("pubDates", {}).get("value") else []
            pub_dates_by_qid[qid] = [d for d in dates if d]
    return pub_dates_by_qid


def verify_true_decade(
    candidates: list[dict], start_year: int, end_year: int
) -> tuple[list[dict], list[str]]:
    """Split candidates into (true-decade survivors, rejected QIDs)."""
    pub_dates_by_qid = fetch_pub_dates([c["qid"] for c in candidates])
    verified = []
    rejected = []
    for c in candidates:
        release_year = resolve_release_year(pub_dates_by_qid.get(c["qid"], []))
        if release_year is None or not (start_year <= release_year <= end_year):
            rejected.append(c["qid"])
            continue
        verified.append(c)
    return verified, rejected


# ---------------------------------------------------------------------------
# Phase 2: enrich candidates with runtime, genre and the enwiki page title,
# batched via VALUES so each call is bounded to a fixed QID list rather
# than joining the whole dataset again.
# ---------------------------------------------------------------------------


def enrich_candidates(qids: list[str], batch_size: int = 50) -> dict[str, dict]:
    enrichment: dict[str, dict] = {}
    for i in range(0, len(qids), batch_size):
        batch = qids[i : i + batch_size]
        values = " ".join(f"wd:{q}" for q in batch)
        query = f"""
        SELECT ?film
               (GROUP_CONCAT(DISTINCT ?runtime; separator="|") AS ?runtimes)
               (GROUP_CONCAT(DISTINCT ?genreLabel; separator="|") AS ?genres)
               (SAMPLE(?enwikiTitle) AS ?title)
        WHERE {{
          VALUES ?film {{ {values} }}
          OPTIONAL {{ ?film wdt:P2047 ?runtime. }}
          OPTIONAL {{
            ?film wdt:P136 ?genre.
            ?genre rdfs:label ?genreLabel. FILTER(LANG(?genreLabel) = "en")
          }}
          OPTIONAL {{
            ?sitelink schema:about ?film;
                      schema:isPartOf <https://en.wikipedia.org/>;
                      schema:name ?enwikiTitle.
          }}
        }}
        GROUP BY ?film
        """
        rows = sparql_query(query)
        for row in rows:
            qid = row["film"]["value"].rsplit("/", 1)[-1]
            runtimes = row["runtimes"]["value"].split("|") if row.get("runtimes", {}).get("value") else []
            genres = row["genres"]["value"].split("|") if row.get("genres", {}).get("value") else []
            title = row.get("title", {}).get("value")
            enrichment[qid] = {
                "runtimes": [r for r in runtimes if r],
                "genres": [g for g in genres if g],
                "enwiki_title": title,
            }
    return enrichment


def resolve_runtime(raw_runtimes: list[str]) -> tuple[float | None, bool]:
    """Return (chosen_minutes, ambiguous). See PLAUSIBLE_RUNTIME_MINUTES."""
    values = []
    for r in raw_runtimes:
        try:
            values.append(float(r))
        except ValueError:
            continue
    in_band = [v for v in values if PLAUSIBLE_RUNTIME_MINUTES[0] <= v <= PLAUSIBLE_RUNTIME_MINUTES[1]]
    if in_band:
        return statistics.median(in_band), len(set(values)) > len(set(in_band))
    if values:
        return None, True  # every value is out of band -- flag, don't guess a conversion
    return None, False  # no runtime statement at all


def resolve_release_year(raw_pub_dates: list[str]) -> int | None:
    """Earliest year across every P577 statement -- see fetch_decade_candidates."""
    years = []
    for d in raw_pub_dates:
        try:
            years.append(int(d[:4]))
        except (ValueError, IndexError):
            continue
    return min(years) if years else None


# ---------------------------------------------------------------------------
# Phase 3: per-film Wikipedia checks -- plot section presence and the
# infobox `starring` field, run only against the enwiki title Wikidata
# gave us (no title-guessing, no search API call).
# ---------------------------------------------------------------------------


def has_plot_section(title: str) -> bool | None:
    data = wikipedia_api({"action": "parse", "page": title, "prop": "sections"})
    if "error" in data:
        return None  # page missing / redirect loop / etc. -- caller records as unresolved
    sections = data.get("parse", {}).get("sections", [])
    return any(s["line"].strip().lower() == "plot" for s in sections)


def fetch_infobox_wikitext(title: str) -> str | None:
    data = wikipedia_api({"action": "parse", "page": title, "prop": "wikitext", "section": "0"})
    if "error" in data:
        return None
    return data.get("parse", {}).get("wikitext", {}).get("*")


def parse_starring_names(infobox_wikitext: str) -> list[dict]:
    """Return [{name, was_piped, was_linked}] for each bulleted entry in `starring`."""
    match = STARRING_FIELD_RE.search(infobox_wikitext)
    if not match:
        return []
    block = match.group(1)
    names = []
    for bullet_line in BULLET_RE.findall(block):
        bullet_line = bullet_line.strip()
        link_match = WIKILINK_RE.search(bullet_line)
        if link_match:
            inner = link_match.group(1)
            if "|" in inner:
                display = inner.split("|", 1)[1]
                names.append({"name": display, "was_piped": True, "was_linked": True})
            else:
                names.append({"name": inner, "was_piped": False, "was_linked": True})
        else:
            plain = re.sub(r"<!--.*?-->", "", bullet_line).strip()
            if plain:
                names.append({"name": plain, "was_piped": False, "was_linked": False})
    return names


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class DecadeReport:
    name: str
    candidates_pulled: int = 0
    passing: list[dict] = field(default_factory=list)
    runtimes_minutes: list[float] = field(default_factory=list)
    ambiguous_runtime_qids: list[str] = field(default_factory=list)
    missing_runtime_qids: list[str] = field(default_factory=list)
    missing_genre_qids: list[str] = field(default_factory=list)
    missing_plot_qids: list[str] = field(default_factory=list)
    no_enwiki_title_qids: list[str] = field(default_factory=list)
    wrong_decade_qids: list[str] = field(default_factory=list)
    genre_label_counts: dict[str, int] = field(default_factory=dict)
    starring_present: int = 0
    starring_absent: int = 0
    starring_counts: list[int] = field(default_factory=list)
    piped_link_count: int = 0
    plain_linked_count: int = 0
    unlinked_count: int = 0


def run_probe(
    candidates_per_decade: int,
    sitelinks_threshold: int,
    film_limit: int | None,
    total_limit: int | None = None,
) -> list[DecadeReport]:
    reports = []
    checked_so_far = 0
    for name, start_year, end_year in DECADES:
        if total_limit is not None and checked_so_far >= total_limit:
            print(f"\n=== {name} ({start_year}-{end_year}) === skipped, --total-limit {total_limit} reached")
            reports.append(DecadeReport(name=name))
            continue

        print(f"\n=== {name} ({start_year}-{end_year}) ===")
        report = DecadeReport(name=name)

        print(f"  fetching candidates (sitelinks >= {sitelinks_threshold}, limit {candidates_per_decade})...")
        candidates = fetch_decade_candidates(start_year, end_year, candidates_per_decade, sitelinks_threshold)
        report.candidates_pulled = len(candidates)
        print(f"  got {len(candidates)} candidates")

        print(f"  verifying true earliest release year for {len(candidates)} candidate(s)...")
        candidates, rejected_qids = verify_true_decade(candidates, start_year, end_year)
        report.wrong_decade_qids.extend(rejected_qids)
        print(
            f"  {len(candidates)} of {report.candidates_pulled} confirmed true {name} release "
            f"({len(rejected_qids)} were re-releases / wrong decade)"
        )

        if film_limit:
            candidates = candidates[:film_limit]
        if total_limit is not None:
            candidates = candidates[: max(0, total_limit - checked_so_far)]
        checked_so_far += len(candidates)

        print(f"  enriching {len(candidates)} candidate(s) with runtime, genre, enwiki title...")
        enrichment = enrich_candidates([c["qid"] for c in candidates])

        for c in candidates:
            qid = c["qid"]
            enr = enrichment.get(qid, {"runtimes": [], "genres": [], "enwiki_title": None})

            runtime_minutes, ambiguous = resolve_runtime(enr["runtimes"])
            if ambiguous:
                report.ambiguous_runtime_qids.append(qid)
            if runtime_minutes is None:
                report.missing_runtime_qids.append(qid)
            else:
                report.runtimes_minutes.append(runtime_minutes)

            genres = enr["genres"]
            if not genres:
                report.missing_genre_qids.append(qid)
            for g in genres:
                report.genre_label_counts[g] = report.genre_label_counts.get(g, 0) + 1

            title = enr["enwiki_title"]
            if not title:
                report.no_enwiki_title_qids.append(qid)
                continue

            has_plot = has_plot_section(title)
            if not has_plot:
                report.missing_plot_qids.append(qid)

            infobox = fetch_infobox_wikitext(title)
            starring_names = parse_starring_names(infobox) if infobox else []
            if starring_names:
                report.starring_present += 1
                report.starring_counts.append(len(starring_names))
                for n in starring_names:
                    if n["was_piped"]:
                        report.piped_link_count += 1
                    elif n["was_linked"]:
                        report.plain_linked_count += 1
                    else:
                        report.unlinked_count += 1
            else:
                report.starring_absent += 1

            passes = (
                runtime_minutes is not None
                and bool(genres)
                and has_plot
            )
            if passes:
                report.passing.append(c)
                if len(report.passing) >= FILMS_PER_DECADE_TARGET:
                    print(f"  reached {FILMS_PER_DECADE_TARGET} passing films, stopping this decade early")
                    break

        reports.append(report)
    return reports


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(reports: list[DecadeReport]) -> None:
    print("\n" + "=" * 70)
    print("COVERAGE SUMMARY")
    print("=" * 70)

    for r in reports:
        print(f"\n--- {r.name} ---")
        print(f"candidates pulled:        {r.candidates_pulled}")
        print(f"passing all filters:      {len(r.passing)} / {FILMS_PER_DECADE_TARGET} target")
        if len(r.passing) < FILMS_PER_DECADE_TARGET:
            print(f"  ** DOES NOT FILL {FILMS_PER_DECADE_TARGET} ** within the candidate pool pulled -- "
                  f"lower --sitelinks-threshold or raise --candidates-per-decade and re-run")
        print(f"wrong decade (re-release): {len(r.wrong_decade_qids)}  {r.wrong_decade_qids[:5]}")
        print(f"missing runtime:           {len(r.missing_runtime_qids)}")
        print(f"ambiguous runtime (unit):  {len(r.ambiguous_runtime_qids)}  {r.ambiguous_runtime_qids[:5]}")
        print(f"missing genre:             {len(r.missing_genre_qids)}")
        print(f"missing plot section:      {len(r.missing_plot_qids)}")
        print(f"no enwiki sitelink at all: {len(r.no_enwiki_title_qids)}")

        if r.runtimes_minutes:
            rt = sorted(r.runtimes_minutes)
            print(
                f"runtime spread (minutes):  min={rt[0]:.0f} "
                f"p25={statistics.quantiles(rt, n=4)[0]:.0f} "
                f"median={statistics.median(rt):.0f} "
                f"p75={statistics.quantiles(rt, n=4)[2]:.0f} "
                f"max={rt[-1]:.0f}"
            )
            under_100 = sum(1 for v in rt if v < 100)
            print(f"  under 100 minutes: {under_100} of {len(rt)} ({100*under_100/len(rt):.0f}%)")

        non_film_suffix = {g: n for g, n in r.genre_label_counts.items() if not g.lower().endswith(" film")}
        print(f"distinct genre labels:     {len(r.genre_label_counts)}")
        if non_film_suffix:
            print(f"  labels NOT ending in \"film\" ({len(non_film_suffix)}):")
            for g, n in sorted(non_film_suffix.items(), key=lambda kv: -kv[1])[:15]:
                print(f"    {g!r}: {n}")

        starring_total = r.starring_present + r.starring_absent
        if starring_total:
            pct = 100 * r.starring_present / starring_total
            print(f"infobox `starring` present: {r.starring_present} / {starring_total} ({pct:.0f}%)")
        if r.starring_counts:
            sc = sorted(r.starring_counts)
            print(f"  names per starring list:  min={sc[0]} median={statistics.median(sc):.0f} max={sc[-1]}")
        link_total = r.piped_link_count + r.plain_linked_count + r.unlinked_count
        if link_total:
            print(
                f"  name formatting: piped_link={r.piped_link_count} "
                f"plain_link={r.plain_linked_count} unlinked={r.unlinked_count} "
                f"(of {link_total} names)"
            )


def write_json(reports: list[DecadeReport], path: str) -> None:
    payload = {
        r.name: {
            "candidates_pulled": r.candidates_pulled,
            "passing_count": len(r.passing),
            "passing_qids": [c["qid"] for c in r.passing],
            "wrong_decade_qids": r.wrong_decade_qids,
            "missing_runtime_qids": r.missing_runtime_qids,
            "ambiguous_runtime_qids": r.ambiguous_runtime_qids,
            "missing_genre_qids": r.missing_genre_qids,
            "missing_plot_qids": r.missing_plot_qids,
            "no_enwiki_title_qids": r.no_enwiki_title_qids,
            "runtimes_minutes": r.runtimes_minutes,
            "genre_label_counts": r.genre_label_counts,
            "starring_present": r.starring_present,
            "starring_absent": r.starring_absent,
            "starring_counts": r.starring_counts,
            "piped_link_count": r.piped_link_count,
            "plain_linked_count": r.plain_linked_count,
            "unlinked_count": r.unlinked_count,
        }
        for r in reports
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates-per-decade",
        type=int,
        default=400,
        help="How many top-by-sitelinks candidates to pull per decade before filtering (default 400, "
        "i.e. a 150-film buffer over the 250 target)",
    )
    parser.add_argument(
        "--sitelinks-threshold",
        type=int,
        default=15,
        help="Minimum Wikidata sitelink count for a film to be considered a candidate at all "
        "(default 15; lower this if a decade can't fill 250)",
    )
    parser.add_argument(
        "--film-limit",
        type=int,
        default=None,
        help="Cap candidates actually checked per decade, for a quick smoke test (e.g. 20)",
    )
    parser.add_argument(
        "--total-limit",
        type=int,
        default=None,
        help="Cap candidates checked across ALL decades combined, stopping early once reached "
        "(e.g. 10, for a small end-to-end test run)",
    )
    parser.add_argument("--json-out", default="probe_results.json", help="Where to write the machine-readable report")
    args = parser.parse_args()

    reports = run_probe(args.candidates_per_decade, args.sitelinks_threshold, args.film_limit, args.total_limit)
    print_report(reports)
    write_json(reports, args.json_out)


if __name__ == "__main__":
    main()
