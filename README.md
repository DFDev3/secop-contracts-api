# SECOP Contracts API

A public REST API to search, filter and summarize Colombian public contracts, built on open data
from [datos.gov.co](https://www.datos.gov.co) (SECOP II).

> **Data scope.** This API serves a **sample**, not national data: every SECOP II contract signed
> in 2024–2025 by public entities located in **Norte de Santander** (about 50,000 contracts).
> Totals and summaries are true for that slice only. They are **not** Colombian national totals.

**Status:** 🚧 in development (Milestone 1: data and foundations).

## What it does (planned)

- `GET /contracts`: list contracts, filtered by entity, date range and amount, with cursor pagination
- `GET /contracts/{id}`: one contract
- `GET /summaries/by-entity` and `GET /summaries/by-month`: aggregated totals
- API-key authentication, input validation, clear error responses, and generated API docs

## Stack

Python 3.13 · FastAPI · SQLAlchemy · Alembic · PostgreSQL · Docker · GitHub Actions · Google Cloud Run

## Documentation

- [Design note](docs/design-note.md): every design decision, the alternatives, and the trade-offs

## Data source and license

Contract data: **SECOP II – Contratos Electrónicos** (dataset `jbjy-vk9h`), published by the
*Agencia Nacional de Contratación Pública – Colombia Compra Eficiente* under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Personal data that the API doesn't
need (bank details, individuals' ID numbers) is deliberately left out.

The code in this repository is licensed under the [MIT License](LICENSE).
