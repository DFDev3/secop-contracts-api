# Design note: SECOP Contracts API

This note records every significant design decision: what we chose, what else we considered,
why the choice fits *this* project, and what we give up. It's written to be read top to bottom
by someone new to the project, including future me.

Status: **Milestone 1 in progress.** Decisions D1–D4 and D7 are made. D5, D6, D8 and D9 come later.
Last updated: Nov 11, 2026, after an outside review of D1–D4 and D7. The review changed: an
explanation of what `uv` replaces (D1); the slice caveat and `scope` field, the exact row count, and
the size check (D3); an honest framing for cursor pagination and limits on its size (D4); and the
verified Neon storage limit and Aiven wording (D7).

---

## 0. The data source

### Which dataset

| Candidate (datos.gov.co id) | Rows (Nov 11, 2026) | Fields | Notes |
|---|---|---|---|
| **SECOP II – Contratos Electrónicos** (`jbjy-vk9h`) | 5,687,382 | 97 | Official, updated daily, one row per contract |
| SECOP Integrado (`rpmr-utcd`) | 22,987,823 | 22 | Merges SECOP I and II, but has far fewer fields |
| SECOP I – Procesos de Compra Pública (`f789-7hwg`) | – | – | The legacy system, organized by process rather than by contract |

**Choice: SECOP II – Contratos Electrónicos.** It's the current national system, has one row per
contract with a contract ID, and includes everything the endpoints need: entity, signing date, amount,
status, type and supplier. It's published by Colombia Compra Eficiente under **CC BY-SA 4.0**, so the
README must credit the source.

Access is through the Socrata SODA API:
`https://www.datos.gov.co/resource/jbjy-vk9h.json`, which supports SQL-like `$select`, `$where`,
`$order`, `$limit` and `$offset`. An optional free *app token* raises the rate limits.

### What the data looks like (profiled Nov 11, 2026)

These numbers come from the full dataset and a sample of 2,000 contracts signed in 2025.

- **IDs are almost unique.** There are 5,687,110 distinct `id_contrato` values across 5,687,382 rows,
  so 272 rows repeat an ID. The importer must handle repeats.
- **67,101 contracts have no signing date** (`fecha_de_firma`).
- **"No Definido" is used instead of empty values.** For example, `ciudad` is "No Definido" in 67%
  of the sample.
- **Inconsistent capitalization:** `estado_contrato` mixes "Cerrado" with "terminado" and "cedido".
- **Amounts:** in the sample, 4% are zero, the median is COP 29M, and the largest is COP 138 billion.
  Amounts can have decimals.
- **Inconsistent dates:** some contracts end before they start, and some start years before they were
  signed. We keep these rows but never assume dates are consistent.
- **Entities:** `codigo_entidad` identifies an entity uniquely. `nit_entidad` doesn't, because several
  entities can share one tax ID (NIT).
- **Free text:** `objeto_del_contrato` (what the contract is for) is capped at 500 characters.
  `urlproceso` arrives as an object `{url: ...}`, not a string.
- **Personal data:** the dataset includes individuals' ID numbers, bank account numbers, and the
  names and ID numbers of supervisors and other officials. See D2 for what we deliberately leave out.

**Contracts per signing year** (the sample design uses these counts):
2021: 560k · 2022: 708k · 2023: 835k · 2024: 882k · 2025: 1,017k · 2026 so far: 953k.

---

## D1. Web framework and project structure

**Choice: FastAPI, with SQLAlchemy 2.0 for database access and Alembic for migrations. Python 3.13
and the `uv` package manager. A small, layered package.**

| Option | Pros | Cons |
|---|---|---|
| **FastAPI** ✅ | Generates OpenAPI docs automatically (a "done when" item). Validates input from type hints with Pydantic. Popular in data and backend job ads | Less built in than Django, so we assemble the pieces ourselves |
| Flask | Very simple and well known | Validation and API docs need extra libraries, which means more glue code |
| Django + DRF | Everything included: ORM, admin, auth | Too much for 4 endpoints. More concepts to learn than the project needs |

**Why it fits:** two of our required features, input validation and generated documentation, come
free with FastAPI. Every endpoint declares its parameters with types, and FastAPI rejects bad input
before our code runs.

**Database access:** we use SQLAlchemy 2.0, with tables defined in Python and queries written with
`select()`. The alternative was raw SQL with `psycopg`. Raw SQL is more direct, but our list endpoint
has up to five optional filters, and building that SQL by gluing strings together is error-prone.
SQLAlchemy builds it safely and still lets us read the generated SQL. **Alembic** keeps the schema
under version control as migrations, the same way Git does for code.

**Sync, not async:** endpoints are plain functions, not `async`. Async only pays off under heavy
concurrent I/O, and it's a common source of beginner bugs. Cloud Run can also scale out with more
instances. We can revisit this in the scale note.

**Tooling:** `uv` **replaces `pip` and virtual environments** with one tool. It installs the Python
version, creates and manages the project's virtual environment (`.venv`), installs dependencies, and
writes a lockfile (`uv.lock`) with the exact version of every package, so CI and Docker install
exactly what runs on my machine. The alternative was `pip` + `venv` + `requirements.txt`. It's more
familiar, but it takes three separate tools, it's slower, and it has no real lockfile.
*Trade-off:* `uv` is newer, so some tutorials assume `pip`. Under the hood it still uses standard
`pyproject.toml` and standard packages, so switching back is easy.

**Structure:**

```
secop-contracts-api/
├── app/
│   ├── main.py          # builds the FastAPI app: routers, error handlers, logging
│   ├── config.py        # settings read from environment variables
│   ├── db.py            # database engine and per-request session
│   ├── models.py        # SQLAlchemy tables (the schema in Python)
│   ├── schemas.py       # Pydantic models: what requests and responses look like
│   ├── auth.py          # API-key check (D5)
│   ├── pagination.py    # cursor encoding/decoding (D4)
│   └── routers/
│       ├── contracts.py # GET /contracts, GET /contracts/{id}
│       └── summaries.py # GET /summaries/by-entity, /by-month
├── scripts/
│   └── import_secop.py  # download and load the sample (D3)
├── migrations/          # Alembic migration files
├── tests/
├── docs/                # this note, the architecture diagram, the scale note
├── docker-compose.yml   # local PostgreSQL
├── Dockerfile
└── pyproject.toml
```

**Trade-off:** organizing files by layer (models, schemas, routers) is the clearest layout for a small
app. If the app grew to many features, organizing by feature would scale better. That's a note for
the scale document.

---

## D2. Database schema and indexes

**Choice: two tables, `entities` and `contracts`. Contracts are linked to entities, store amounts as
exact decimals, and leave out personal data the API doesn't need.**

```
entities                                  contracts
───────────────────────────────           ───────────────────────────────────────────
id            bigint  PK  (codigo_entidad) id                text  PK  (id_contrato)
name          text    NOT NULL             entity_id         bigint NOT NULL → entities.id
nit           text                         reference         text
department    text                         process_id        text
city          text                         status            text      (normalized)
level         text    (orden)              contract_type     text
sector        text                         procurement_method text     (modalidad)
branch        text    (rama)               description       text      (objeto_del_contrato)
                                           signed_at         timestamptz NOT NULL
                                           start_date        date
                                           end_date          date
                                           value             numeric(18,2) NOT NULL, ≥ 0
                                           amount_paid       numeric(18,2)
                                           supplier_name     text
                                           supplier_doc_type text
                                           supplier_nit      text      (only when it's a company NIT)
                                           is_sme            boolean   (es_pyme)
                                           url               text
                                           source_updated_at timestamptz
                                           imported_at       timestamptz NOT NULL
```

**Why a separate `entities` table:** summaries are grouped by entity, and users filter by entity.
Storing each entity once means its name lives in one place. Grouping uses a numeric key instead of
long names. The table is also a clean place for Projects 2–4 to attach more entity data.
*Alternative:* one flat table mirroring the source. It's simpler (no join), but it repeats the entity
details on every row and makes "list all entities" awkward.
*Trade-off:* the list and detail endpoints need a join. At this size the join is cheap, and the
summaries need it anyway.

**Why suppliers stay inside `contracts`:** no endpoint groups by supplier, so a `suppliers` table would
add complexity we don't use yet. That can come in Project 2.

**Data types:**
- Money is `numeric(18,2)`, never floating point, because floats can't represent decimal amounts
  exactly (0.1 + 0.2 ≠ 0.3). 18 digits covers the largest values in the dataset.
- Categories (status, type, method) are plain `text` with cleaned values, not PostgreSQL `ENUM`s.
  The source adds new values over time, and each new enum value would need a migration.
- `signed_at` is `NOT NULL`. Every query sorts and filters by it, and the import only takes contracts
  with a signing date.
- Constraint: `value >= 0`. The database itself rejects impossible data.

**Personal data we deliberately leave out:** bank names and account numbers. The names and ID numbers
of legal representatives, supervisors and officials who authorize spending. The ID numbers of
individual suppliers (*cédulas*). We keep a supplier's tax ID only when the supplier is a company (NIT).
The source is public, but republishing personal data the API doesn't need is a risk with no benefit.
This is *data minimization*.

**Indexes:** each one serves a specific query.

| Index | Serves |
|---|---|
| `contracts` primary key on `id` | `GET /contracts/{id}`: finding one contract instantly; also stops duplicates |
| `entities` primary key on `id` | the join from contracts to entities |
| `contracts (entity_id, signed_at DESC, id DESC)` | listing one entity's contracts by date (the most common filter), paginated; also covers the foreign key and the summary by entity |
| `contracts (signed_at DESC, id DESC)` | listing *all* contracts by date with a date range, paginated; also covers the summary by month |

**No index for amount (yet):** an amount filter is almost always combined with an entity or a date,
and those indexes narrow the rows first. Every index slows down writes and takes space, so we add
indexes only when `EXPLAIN` shows a query needs one (milestone 2, step 7).
*Trade-off:* a query filtered *only* by amount scans the whole table. With 50k rows that takes a
few milliseconds. With the full dataset it would need its own index (scale note).

**Why `id` is the last column in the composite indexes:** many contracts share the same signing time,
so `id` breaks ties. That gives a strict order, which cursor pagination needs (D4).

---

## D3. How to download and load the sample

**Choice: a two-step Python script. Step 1 downloads pages from the SODA API to raw JSON files.
Step 2 cleans the data and *upserts* it into PostgreSQL. The sample is all contracts from one
department signed in 2024 and 2025.**

**Sample design:** about 50,000 contracts should be a slice that *makes sense*, not an arbitrary cut.

| Option | Problem |
|---|---|
| The first 50k rows | Arbitrary and biased (whatever order the API returns) |
| The latest 50k | Only covers about 2–3 weeks, so monthly summaries would be meaningless |
| A random 50k | SODA has no random sampling, and random rows make every entity total wrong |
| **One department, two full years** ✅ | Every contract for those entities in that period, so entity and monthly summaries are *true* numbers |

Department sizes for 2024–2025: Norte de Santander 50,724, Tolima 53,135, Magdalena 53,649,
Meta 45,749. **Default: Norte de Santander**, with 50,724 rows, the closest to the brief's
"about 50,000". After duplicate IDs are merged and invalid rows rejected, the final count will be
slightly lower. The importer prints the exact number, and we record it here. The department and the
date range are script parameters, so changing them doesn't change any code.

**What the slice means:**
- The `departamento` field is the **contracting entity's** department, not where the work is done.
  So the sample is *"every SECOP II contract signed in 2024–2025 by public entities located in
  Norte de Santander"*. It is **not** "all contracts carried out in Norte de Santander", and it is
  **not** national data.
- Entity and monthly totals are true **for that slice only**. To keep anyone from reading them as
  Colombian national totals:
  - The README states the slice in its first lines.
  - Every summary response includes a `scope` object (source dataset, department, date range,
    import date), so the caveat travels with the numbers.
  - The API docs say the same in each endpoint's description.

**Size check:** the cleaned table is about 25 columns with descriptions of up to 500 characters, which
works out to an estimated **50–150 MB** including indexes. That's well inside Neon's 1 GB limit, and
would even fit under 0.5 GB. We measure the real size with `pg_total_relation_size` after the local
load (milestone 1, step 8) and record it here.

**Step 1: download.** Request pages of 5,000 rows, filtered with `$where` on department and
signing date and sorted by `id_contrato` so paging is stable. Save each page to `data/raw/`, which is
excluded from Git. Use the app token if one is set (`SOCRATA_APP_TOKEN`). Retry with a delay if the
API fails.

**Step 2: clean and load.**
- Turn "No Definido" and empty strings into `NULL`. Trim spaces. Normalize capitalization for
  categories (so "terminado" becomes "Terminado").
- Parse dates and amounts (`Decimal`), and extract the URL from its `{url: ...}` object.
- Drop the personal fields listed in D2.
- Reject rows that break rules (no signing date, negative value), and print a summary of rejected rows.
- **Upsert:** `INSERT ... ON CONFLICT (id) DO UPDATE`. When an ID appears twice, the version with the
  latest `source_updated_at` wins. The script can be re-run safely: running it twice gives the same
  result as running it once. Entities are upserted first.

**Why two steps:** the network is the unreliable part. With raw files on disk, we can fix the cleaning
code and reload in seconds without downloading again. It also mirrors the "extract, then
transform/load" pattern Project 2 will use.

**Alternatives:** the CSV export (one big file, but no paging or retries, and an all-or-nothing
download). `pandas` (convenient, but a heavy dependency the API doesn't need). PostgreSQL `COPY`
(the fastest bulk load, but it can't easily upsert). At 50k rows, batched inserts take seconds, so
`COPY` is a note for the scale document.

---

## D4. Pagination style

**Choice: cursor (keyset) pagination, ordered by newest signing date first, with an opaque `cursor`
token.**

How it works: each response returns up to `limit` contracts (default 20, maximum 100) and a
`next_cursor`. The cursor encodes the `(signed_at, id)` of the last item. The next request asks for
"rows that come after this one", and the `(signed_at, id)` index (D2) jumps straight to that point.

| | Offset (`?page=37`) | **Cursor (`?cursor=...`)** ✅ |
|---|---|---|
| Simplicity | Simplest to build and understand | A little more code: encode/decode the cursor |
| Speed on deep pages | Slows down: the database still reads and discards every skipped row | Same speed on page 1 and page 10,000 |
| Data changing between requests | Rows can be skipped or shown twice when new rows arrive | Stable: always continues after the last row seen |
| Jumping to page N, showing a total | Easy | Not possible directly. A count needs a separate query |

**Why cursor for this project:** Project 2 will load the full 5.7M-row dataset and keep adding contracts
every day. Those are exactly the two situations where offset pagination breaks: deep pages get slow,
and results shift between pages. Choosing cursor now means the API contract doesn't change later.

**Honest framing:** for the 50k sample alone, offset pagination would be fine, and it's the simpler
option. Cursor pagination is chosen **because of where the data is going** (Project 2), not because
the sample needs it.

**Trade-offs:**
- No "jump to page 37" and no "page 3 of 2,500". The frontend uses a "Load more" button, which suits a
  search screen anyway.
- More code than offset: encoding and decoding the cursor, and a slightly harder `WHERE` clause.

**Keeping it small:**
- **One** fixed sort order (newest first), with no `sort=` parameter. Each extra sort order would
  need its own cursor format and its own index.
- The cursor is base64 of `signed_at|id`, so it's opaque and clients can't depend on its contents.
- Paging is a single comparison: `WHERE (signed_at, id) < (:cursor_signed_at, :cursor_id)`.
- All of it lives in one small module, `app/pagination.py`, with its own tests. The goal is under
  about 40 lines.

---

## D5. API-key authentication

*Sketch only. Decided in milestone 3.* The likely design: clients send `X-API-Key`. We store only a
**hash** of each key, so a database leak doesn't leak usable keys. A small CLI script creates keys.
The docs and health endpoints stay public.

---

## D7. Where to host PostgreSQL (free tier)

**Choice: Neon, free plan.** Limits checked on the official pages on Nov 11, 2026. Re-check them at
signup, because free plans change.

| Host | Free storage | Idle behavior | Other limits |
|---|---|---|---|
| **Neon** ✅ | **1 GB per project**, 20 GB per account | Compute scales to zero after 5 min idle (this can't be turned off on Free) and **starts again on the next query** | 100 compute-hours/month per project, 5 GB transfer/month. No credit card |
| Supabase | 500 MB | A project with too little database activity over a week is **paused**, and you restore it manually | 2 free projects. Also bundles auth and storage, which we don't need |
| Aiven | 1 GB | Aiven *"reserves the right to"* **power off** free services with "no continuative activity", after a notification. No time is given | 20 connections, no connection pooling |

**Storage check, resolved:** some Neon pages mention 0.5 GB. Those are older posts. The current
[plans page](https://neon.com/docs/introduction/plans) and
[pricing page](https://neon.com/pricing) both say *"1 GB/project, 20 GB account total"* (both last
updated Nov 4, 2026). Our estimated 50–150 MB fits even under the old 0.5 GB limit. We confirm on the
sign-up page and with the measured size after loading.

**Why Neon:** this is a portfolio API, so it's idle most of the time and must still work when a
recruiter opens it weeks later. Supabase and Aiven can pause or shut down idle free databases. Neon
only *suspends compute*, and the first query wakes it, so nobody needs to log in and restore anything.
Our data is small: the estimate is about 100–150 MB including indexes (we'll measure it after the
local load). It's also plain PostgreSQL, so moving off Neon later means changing one connection string.

**Trade-off:** the first request after an idle period is slower while the compute wakes up. Neon
says resuming takes
[a few hundred milliseconds](https://neon.com/faqs/best-free-low-cost-managed-postgres-services).
On top of that, Cloud Run may start a new container (its own *cold start*). So the first request
after a quiet period can take noticeably longer than the rest. For a demo API that's acceptable, and
the README will mention it. We'll measure it in milestone 4. 100 compute-hours/month is far more than
a demo needs.

**Local development** uses PostgreSQL in Docker (`docker-compose.yml`), on the same major version as
Neon, so local and cloud behave the same.

---

## D6, D8, D9: pending

- **D6, testing approach:** milestone 3.
- **D8, Docker setup and CI pipeline:** milestones 3 and 4.
- **D9, errors and logs:** milestones 2 and 3.

---

## Free-tier facts (checked Nov 11, 2026)

Re-check before creating anything. Free plans change.

- **Cloud Run** (request-based billing, the default): 2 million requests, 180,000 vCPU-seconds and
  360,000 GiB-seconds free per month, shared across a billing account. Source:
  [Cloud Run pricing](https://cloud.google.com/run/pricing).
- **Neon free:** 1 GB storage per project (20 GB per account), 100 compute-hours per month, scale to
  zero after 5 minutes, no credit card. Sources: [Neon plans](https://neon.com/docs/introduction/plans),
  [Neon pricing](https://neon.com/pricing), both updated Nov 4, 2026.
- **Supabase free:** 500 MB per project. Paused after a week of too little database activity,
  restored manually. Sources: [Supabase pricing](https://supabase.com/pricing),
  [Supabase free-plan pausing](https://supabase.com/docs/guides/platform/free-plan-pausing).
- **Aiven free:** 1 GB, 20 connections, no pooling. May power off services with no continuing
  activity after a notification. No time is given. Source:
  [Aiven free tier](https://aiven.io/docs/products/postgresql/concepts/pg-free-tier), updated
  Sep 8, 2026.
- **Google Cloud signup:** new accounts may be asked for a prepayment or card verification before
  the free tier applies. Check this before deploying.
