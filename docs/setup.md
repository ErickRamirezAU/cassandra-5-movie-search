# Set up cMovie: Astra DB, the loader and an AI Studio key

Every post in this series links here instead of repeating setup. Do this once,
then come back to whichever week sent you.

> I'm a Developer Advocate at DataStax, now an IBM company, and I wrote this
> series as part of that role. It runs on Astra DB, a DataStax product, so the
> setup stays out of your way. Everything it teaches is an Apache Cassandra 5.0
> feature you can also run on open source Cassandra.

## Prerequisites

You need three things on your own computer before you start. Each link below
has the setup instructions for your system.

1. **A Python virtual environment.** The loader needs Python 3.10 or later, and
   you run it inside a virtual environment, which you create in the clone of the
   series repo in the next section. See [Virtual Environments and
   Packages](https://docs.python.org/3/tutorial/venv.html) in the Python docs
   for how to create and activate one.
2. **A free GitHub account.** See [Creating an account on
   GitHub](https://docs.github.com/en/get-started/start-your-journey/creating-an-account-on-github).
3. **The git command line tool.** See [Installing
   Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git).

No local Cassandra, no JVM, no `cqlsh` install and no Docker. You won't need a
key for the film data itself, because [Wikidata](https://www.wikidata.org) and
[Wikipedia](https://en.wikipedia.org) are open.

## 1. Clone the series repo

Clone the repo and move into it:

```bash
git clone https://github.com/ErickRamirezAU/cassandra-5-movie-search.git
cd cassandra-5-movie-search
```

Create and activate a virtual environment in this directory, as described in
the Python guide linked under Prerequisites. Then install the one dependency:

```bash
pip install -r requirements.txt
```

The directory you just moved into is the root directory of the clone. The
secure connect bundle and the `.env` file later on both go here.

## 2. Astra DB

### 2.1 Create a database

1. [Sign up for a free Astra DB account](https://ibm.biz/~JWxojhhI5). It
   doesn't need a credit card.
2. In the Astra portal, click **Create database**.
3. Enter the database name `cmovies`. Use this name for the whole series, so
   the steps in every post match what you see in the portal.
4. Select these options:
   - Type: **Serverless (vector)**
   - Provider: **Amazon Web Services**
   - Region: **us-east-2**

   AWS us-east-2 is the only region available on the free tier.
5. Click **Create database** and wait for its status to change to **Active**.
   A new database can take 60 to 90 seconds to initialise before it goes
   **Active**.

Free accounts have no PCU groups, so there's nothing to choose there.

You don't create a keyspace. Astra provides one called `default_keyspace`, and
every post in the series uses it.

#### 2.1.1 Open the CQL console

Weeks 1, 3, 5, 7 and 9 run entirely in the CQL console. To open it, click your
database's name in the Astra portal, then click the **CQL console** button at
the top right of the page.

Each time you open the console, select the keyspace first:

```sql
USE default_keyspace;
```

That's all the database setup there is. This series doesn't teach
administration, and Astra takes care of it.

### 2.2 Generate a token

The loader and the build weeks authenticate with a token.

1. After you create the database, the Astra portal shows a dialog box for
   generating an application token. Use it now. If you closed it or didn't
   create a token, click your database's name in the Astra portal, then click
   the **Generate token** button on the right side of the page.
2. Use the default **Database Administrator** role, and name the token
   `token-cmovies-dbadmin`. Keep the default expiration, **Never expire**.
3. Copy the token value, which starts with `AstraCS:`, and save it somewhere
   safe. Astra shows it only once, so you can't get it back later. If you lose
   it, generate a new token.
4. Treat it like a password. Keep it out of screenshots, commits and shared
   shells.

### 2.3 Download the secure connect bundle

The loader and the Python drivers connect through a secure connect bundle, a
zip file for your database.

1. In the Astra portal, click the Astra icon at the top left of the page, then
   click the `cmovies` database.
2. In the **Region** panel on the right side of the page, click the three
   vertical dots next to **US East** and select **Download SCB**.
3. Save the zip in the root directory of your clone of the series repo. Keep the
   zip as it is. Don't unpack it.

For more detail, see [Download SCBs with the Astra
portal](https://docs.datastax.com/en/astra-db-serverless/databases/secure-connect-bundle.html#download-scbs-with-the-astra-portal)
in the DataStax docs.

The downloaded file is named `secure-connect-` followed by the database name,
so for this series it's `secure-connect-cmovies.zip`. The loader looks for that
name by default.

### 2.4 Resuming a hibernated database

Free tier Astra databases are hibernated after 48 hours of inactivity. A
hibernated database shows a **Hibernated** flag next to its name in the Astra
portal. If a post's queries stop working after a break, check for that flag
first.

To resume it, follow the [Hibernated database
status](https://docs.datastax.com/en/astra-db-serverless/databases/database-statuses.html#hibernated)
section of the DataStax docs.

## 3. Google AI Studio

You can skip this until week 3. From then on, the loader turns each film's plot
into an embedding with Gemini, and weeks 6 and 10 use a Gemini model to answer
questions.

1. The free tier only needs a Google account. Follow Google's [Gemini API
   getting started guide](https://aistudio.google.com/docs/get-started) to
   create an API key in Google AI Studio.
2. Copy the key and save it. You add it to your `.env` file as
   `GEMINI_API_KEY` in the next section.

One thing to know before you use it. Google's terms for the free tier say that
content sent to it is used to provide, improve and develop Google products,
that human reviewers may read inputs and outputs, and that you shouldn't submit
sensitive or confidential information to it. This series only ever sends public
Wikipedia plot text and film titles.

### 3.1 Checking your own rate limits

Free tier limits change and Google no longer publishes a table of them, so this
series doesn't quote numbers. Check your own limits in Google AI Studio, and
expect them to differ from anyone else's.

## 4. Environment variables

### 4.1 Create the `.env` file from the example

The loader reads your token from a file called `.env` in the root directory of
your clone of the series repo, next to `loader.py`. The repo includes a sample,
`.env.example`, so you only fill in the blanks.

1. In the root directory of the clone, copy `.env.example` to a new file called
   `.env`.
2. Open `.env` in a text editor and replace `AstraCS:paste-your-token-here`
   with the token value you saved in section 2.2.
3. Leave everything else as it is:
   - `ASTRA_DB_KEYSPACE` is already `default_keyspace`.
   - If you have a Google AI Studio key, paste it after `GEMINI_API_KEY=`.
     Otherwise leave it empty until week 3.
   - The `CASSANDRA_` settings are only for a plain Apache Cassandra cluster.
     Leave them empty when you use Astra DB.

The repo's `.gitignore` excludes `.env`, so git won't commit it. Treat it like
the token itself: don't share it or paste it into screenshots.

## 5. Load the movies dataset

The loader pulls 1,000 films from [Wikidata](https://www.wikidata.org) and
[Wikipedia](https://en.wikipedia.org), generates the three
`cmovie_` columns, and writes everything to a `movies` table in
`default_keyspace`.
The same loader serves all ten posts, so you only do this once. Week 3 adds the
embedding step.

Run the loader from the repo's root directory, where you saved the bundle and
created `.env`. It reads your token from `.env` and looks for
`secure-connect-cmovies.zip` in the same directory:

```bash
python loader.py
```

If your bundle has a different name or location, pass its path with
`--scb /path/to/your-bundle.zip`.

What to expect:

- The loader collects and cleans all the films first, and only connects to
  Astra once that's done. A missing token is caught immediately,
  before any of that runs. A wrong token, though, is still
  only discovered once the loader tries to connect, after the slow part.
  A full default run (1,000 films, 250 per decade) takes about an hour and a
  quarter, 1h13m end to end. It queries Wikimedia one
  request at a time to stay within their limits, so it isn't instant.
- It prints a status line every 60 seconds while it's working, so a long run
  doesn't look stalled: elapsed time, how many candidates it's worked through,
  how many films it's accepted, and how many it's skipped. Change how often
  with `--progress-interval`, in seconds:

  ```bash
  python loader.py --progress-interval 30
  ```

- It finishes with a line like `all 1000 rows inserted`. If any insert fails it
  prints the film and the error, and a count of failures.
- Writing the same film twice overwrites it, because a Cassandra `INSERT` is an
  upsert, so re-running the loader doesn't create duplicates. With the default
  settings it picks the same films every time. If you raise
  `--films-per-decade` close to the size of the candidate pool, the last few
  films it picks can change between runs, because some films are tied in the
  ranking.

If you'd rather try a smaller batch first, pass `--films-per-decade`:

```bash
python loader.py --films-per-decade 5
```

### 5.1 Check the load

In the CQL console:

```sql
SELECT movie_id, title, release_year FROM movies LIMIT 5;
```

You should see five films, each keyed by its Wikidata ID such as `Q25188`.

### 5.2 Where the data comes from

| Data | Source | Licence |
| --- | --- | --- |
| Title, release year, genres, runtime | [Wikidata](https://www.wikidata.org) | [CC0](https://creativecommons.org/publicdomain/zero/1.0/), no attribution needed. Credited anyway |
| Plot text | [English Wikipedia](https://en.wikipedia.org) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Reuse needs a [link to the article](https://en.wikipedia.org/wiki/Wikipedia:Reusing_Wikipedia_content) and a [licence notice](https://creativecommons.org/licenses/by-sa/4.0/) |
| Starring actors | English Wikipedia infobox | CC BY-SA 4.0 |
| `cmovie_rating`, `cmovie_votes`, `cmovie_popularity` | Generated by the loader | Made up |

The three `cmovie_` numbers are fictional, invented for the cMovie app. They
are not real audience scores. They're seeded from each film's Wikidata ID, so
everyone who loads the same film gets the same numbers and the results in each
post match what you see.
