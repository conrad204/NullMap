---
title: nullMap API
emoji: 🧪
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
---

# nullMap API

FastAPI backend for nullMap. It reads a prebuilt Elasticsearch index and serves
`/health`, `/ready`, `/search`, `/search/stream`, `/studies/{id}` and the other API routes.
The web frontend is hosted separately and calls this Space.

Configuration comes from the Space's **Variables and secrets** settings; nothing secret is in this repo.

| Name | Kind | Value |
| --- | --- | --- |
| `ELASTIC_URL` | secret | Elasticsearch endpoint |
| `ELASTIC_API_KEY` | secret | Elasticsearch API key |
| `OPENAI_API_KEY` | secret | OpenAI key |
| `ELASTIC_INDEX` | variable | `studies-hypertension-kidney-s3` |
| `CORS_ORIGINS` | variable | JSON list of allowed frontend origins, e.g. `["https://your-app.vercel.app"]` |

This folder is generated from the main repository by `deploy/hf-space/sync.sh`; edit the code there, not here.
