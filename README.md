# FastAPI Health API

API minimale FastAPI déployable sur Cloudflare Python Workers.

## Endpoint

```http
GET /health
```

Réponse attendue :

```json
{"status":"ok"}
```

## Prérequis

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Node.js
- pnpm

## Installation

```bash
uv sync
pnpm install
```

## Développement local

```bash
uv run pywrangler dev
```

Puis tester :

```bash
curl http://localhost:8787/health
```

## Qualité et tests

```bash
uv run ruff check .
uv run pytest
```

## Déploiement Cloudflare

Après authentification avec Cloudflare :

```bash
uv run pywrangler deploy
```
