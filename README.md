<p align="center">
  <img src="brand/noosphere-logo.gif" alt="Noosphere" width="180" height="180">
</p>

# Noosphere — Proof of Concept

A minimal, runnable proof of concept for **Noosphere**, an infrastructure for
collective intelligence and capital allocation that weights collective decisions
by **reasoning quality**, not by headcount or token holdings.

This repository exists to demonstrate that the core architecture is **real and
runnable** — not to be the full protocol. It is deliberately small.

## What this PoC demonstrates

1. **A typed argument graph.** Claims are nodes; relations are typed edges
   (`support`, `refute`, `qualify`). Stored in Postgres (async).
2. **Proof of Intelligence (PoI) scoring.** When an argument is added, the system
   calls an LLM and returns a **structured, multi-criterion score** (clarity,
   depth, counterargument handling, evidence, awareness of limits) — never a
   single opaque number. This is the *semantic layer only*.
3. **Vote-weight computation.** A pure function maps PoI scores to vote weights,
   with **sub-linear** capital influence so that influence tracks argument
   quality rather than spending power.
4. **Visualization.** A force-directed graph front-end reads the live backend.

## What this PoC is NOT (by design)

Sybil-resistance is **not** claimed to be solved by the semantic layer. In the
full design it is an *emergent property* across four layers — semantic,
identification, economic, and social. LLM-generated text invalidates the
semantic layer alone, which is precisely why the other layers exist.

Out of scope for this PoC: blockchain / smart contracts, hardware, graph DBs,
authentication, real staking or token mechanics, multi-user concurrency,
production hardening. These are intentional omissions, not gaps to be hidden.

## Storage: async Postgres

The PoC runs on **Postgres via asyncpg**, not SQLite: many users must be able to
WRITE concurrently, and SQLite serialises every writer behind one lock. All
request handlers are `async`, and the blocking LLM scoring call is pushed to a
worker thread so one slow score never stalls the event loop.

Connection string comes from `DATABASE_URL` in `.env`
(default `postgresql://noosphere:noosphere@localhost/noosphere`).

## Run it

```bash
# 1. Postgres (once): install + create the db/user
sudo apt install -y postgresql
sudo -u postgres psql -c "CREATE USER noosphere WITH PASSWORD 'noosphere';" \
                      -c "CREATE DATABASE noosphere OWNER noosphere;"

# 2. deps + seed + launch
pip install -r requirements.txt
python -m app.seed                       # wipes + seeds ~11 typed nodes / 2 topics
./run.sh                                 # detached; reads .env, serves :8000
# open http://localhost:8000
```

### UI

- `/` — **tree view** (primary): topics are top-level folders; replies nest
  underneath by edge type. Open a node to see its PoI breakdown, PoI-weighted
  reactions, and (on a topic root) the clustered **positions/pools**.
- `/graph.html` — the force-directed visualization, kept as an optional mode.

### API

| Method | Path            | Purpose                                            |
|--------|-----------------|----------------------------------------------------|
| GET    | `/api/graph`    | Full graph (`nodes` + `links`) for the front-end   |
| POST   | `/api/argument` | Add an argument, score it via LLM, return the node |
| POST   | `/api/edge`     | Connect two nodes with a typed edge                |
| GET    | `/api/weights`  | Vote-weights computed from current PoI scores      |

## Tests

```bash
./run-tests.sh              # everything, including the Postgres-backed tests
./run-tests.sh tests/test_auth.py   # or a single file
./run-tests.sh --clean      # throw the test cluster away
```

The database tests wipe the database they run against, so they need one of
their own. `run-tests.sh` creates a **separate Postgres cluster in your home
directory** (`~/.local/share/noosphere-pgtest`, port 5433) and points
`TEST_DATABASE_URL` at it: no root, no CREATE DATABASE grant in the system
Postgres, and nothing shared with your dev database. Override with
`NOOSPHERE_TEST_PGDIR` / `NOOSPHERE_TEST_PGPORT`.

Plain `python -m pytest tests/ -q` still works, but without
`TEST_DATABASE_URL` every database test SKIPS — which is how 45 of them sat
unnoticed until 2026-08-25. If you see a skip count, you are not running the
full suite.

The vote-weight formula is covered by isolated tests that run without an LLM or
a database, so its correctness is independently verifiable.

## License & Trademark

Код распространяется под лицензией **AGPL-3.0-only** — см. [LICENSE](LICENSE).

**Название и логотип Noosphere — товарные знаки и под AGPL не подпадают.**
Открытость кода не даёт права использовать имя и логотип Noosphere.
Файлы бренда лежат в отдельной папке [`brand/`](brand/) и не покрываются AGPL;
их использование регулируется политикой [TRADEMARK.md](TRADEMARK.md).
