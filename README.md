# WikiPulse — Live AI Dashboard for Wikipedia

WikiPulse is a real-time media-monitoring dashboard that streams live Wikipedia edits from Wikimedia EventStreams (`https://stream.wikimedia.org/v2/stream/recentchange`), computes rolling editorial telemetry, explains what is trending in plain English using AI, and flags edits that look like vandalism with concise reasons.

---

## Features

- **Real-Time Ingestion (`backend/stream_client.py`)**
  - Connects to Wikimedia's public SSE endpoint with a compliant `User-Agent`, `Last-Event-ID` tracking, and exponential backoff reconnects.
  - Supports **Offline Replay Mode** backed by `sample_events.jsonl` (switchable via `.env` or directly in the dashboard header).
- **Rolling Statistics Engine (`backend/app/rolling_stats.py`)**
  - **Edits per minute** (exact rolling 60-second window + 10-minute UTC bucketed velocity chart).
  - **Top 10 most-edited pages** in the rolling 5-minute window (ranked by edit count, unique editors, net byte delta, and vandalism alerts).
  - **Wiki filter** across all panels (All Wikis or any active wiki like `enwiki`, `frwiki`, `dewiki`, `wikidatawiki`, `commonswiki`).
- **AI Features (`backend/app/ai_service.py`)**
  - **Pulse Summary**: Plain-English editorial briefing + highlights of current Wikipedia trends, cached per wiki filter and refreshable on demand.
  - **Vandalism Flag**: Two-stage pipeline — instant deterministic pre-screening (<1 ms) + optional asynchronous LLM verification when an API key is configured, highlighting suspicious edits and vandalism rollbacks with severity, confidence, and reason.
  - Supports **OpenAI** (and OpenAI-compatible endpoints like Ollama/OpenRouter), **Anthropic**, **Google Gemini**, and a **Local Heuristic AI fallback** so the app works out-of-the-box without an API key.
- **React + TypeScript Dashboard (`frontend/`)**
  - Live WebSocket streaming (`/ws`) with auto-reconnect, per-client wiki filtering, Pause/Resume queue buffer, search, and "Flagged Only" vandalism filter (`frontend/src/components/LiveFeed.tsx`).
- **Optional Extras Included**
  - **Docker & `docker-compose.yml`** setup with multi-stage frontend Nginx build + WebSocket reverse proxy.
  - **End-to-End UI Test in C# + Selenium WebDriver** (`e2e-selenium-csharp/`).

---

## Architecture

```text
Wikimedia SSE Stream ──┐
 (or sample_events.jsonl)
                       ▼
         backend/stream_client.py
                       │
                       ▼
        FastAPI Runtime (app/main.py)
         ├── Stage-1 Vandalism Heuristic (<1ms)
         ├── Stage-2 Async LLM Verify (budget-guarded)
         ├── RollingStats (60s EPM, 5m Top 10, 10m chart)
         ├── AIService Pulse Summary (cached per wiki)
         └── ConnectionManager (WebSocket /ws + REST /api/*)
                       │
                       ▼  WebSocket (snapshot / edit / stats / pulse / vandalism_update)
      React + TypeScript Dashboard (frontend/)
         ├── HeaderBar (Live/Replay toggle, Wiki filter, Quick chips)
         ├── PulseSummaryCard (AI trend briefing + Refresh)
         ├── VelocityChart (KPIs + 10-min SVG chart)
         ├── TopPagesTable (Top 10 in last 5 min)
         └── LiveFeed.tsx (Live feed, vandalism callouts, search, pause)
```

---

## Quick Start (Local Development)

### Prerequisites

- **Python 3.11+** (tested on Python 3.12 with `uv` or `venv`)
- **Node.js 20+** and `npm`
- *(Optional)* **Docker Desktop** for containerized run
- *(Optional)* **.NET 10 SDK + Google Chrome** for the C# Selenium E2E suite

### 1. Configure Environment (Optional LLM Key)

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

- **Without an API key**: WikiPulse automatically uses its built-in heuristic AI engine for both Pulse Summaries and Vandalism Flags.
- **With an API key**: Set `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` in `.env` (leave `LLM_PROVIDER=auto`). You can also point `OPENAI_BASE_URL` at a local Ollama or OpenAI-compatible server.

### 2. Run the Python Backend

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

> **Port note**: If port `8000` is already in use on your machine, start uvicorn on another port (e.g. `--port 8001`) and pass `VITE_BACKEND_URL=http://127.0.0.1:8001` when starting the frontend.

To start directly in offline replay mode instead of live Wikimedia SSE:

```bash
# Windows PowerShell:
$env:WIKIPULSE_MODE="replay"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 3. Run the React + TypeScript Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** in your browser.

---

## Running with Docker Compose

```bash
docker compose up --build
```

- Dashboard UI: **http://localhost:8080** (Nginx proxies `/api/*` and `/ws` to the FastAPI container)
- Backend API & Swagger docs: **http://localhost:8000/docs**

---

## Running Tests

### Backend Unit & WebSocket Integration Tests (Pytest)

```bash
cd backend
pytest -v
```

Covers:
- Exact 60-second EPM and 5-minute Top 10 sliding-window expiration & tie-breaking (`test_rolling_stats.py`)
- SSE line framing, heartbeats, malformed JSON resilience, IPv4/IPv6/temp-account detection, and `sample_events.jsonl` replay (`test_stream_client.py`)
- Vandalism heuristics, rollback classification, rate-limit budget guardrails, JSON fence parsing, and mocked OpenAI LLM responses (`test_ai_service.py`)
- FastAPI REST routes and WebSocket snapshot, filter switching, pulse refresh, and ping/pong (`test_api_ws.py`)

### Frontend Unit & Component Tests (Vitest + React Testing Library)

```bash
cd frontend
npm run typecheck
npm test
npm run build
```

### End-to-End UI Test (Selenium with C# & xUnit)

**Why Selenium with C#?** It was listed as the preferred option in the brief, integrates cleanly with xUnit, and Selenium 4's built-in Selenium Manager automatically resolves the matching ChromeDriver binary with zero manual driver setup.

With the backend and frontend running (default `http://localhost:5173`):

```bash
cd e2e-selenium-csharp
dotnet test --verbosity normal
```

To target a different URL (e.g., Docker Compose on port `8080`):

```bash
# Windows PowerShell:
$env:WIKIPULSE_URL="http://localhost:8080"; dotnet test
```

The E2E suite verifies live edit streaming, non-zero EPM, Top 10 rendering, Replay mode switch, vandalism flag banners, AI Pulse Summary refresh, wiki filtering (`enwiki`), "Flagged Only" filtering, Pause/Resume, switching back to Live Wikimedia SSE, and zero severe browser console errors.

---

## Decisions & Assumptions

1. **Starter Files Note**: The workspace provided only the assessment brief PDF (`sample_events.jsonl`, `LiveFeed.tsx`, and `stream_client.py` were not present on disk). I created `backend/stream_client.py`, `frontend/src/components/LiveFeed.tsx`, and recorded a real 80-event `sample_events.jsonl` directly from `https://stream.wikimedia.org/v2/stream/recentchange` (augmented with 5 schema-accurate vandalism/revert fixtures for deterministic offline testing).
2. **Two-Stage Vandalism Detection (Protecting the API Budget)**: Wikipedia produces 30–100+ edits per second globally. Sending every raw edit to an LLM would immediately exhaust rate limits and the assignment spending cap. Instead, Stage 1 runs a sub-millisecond deterministic pre-screener (page blanking, unattributed large deletions, anonymous/temporary accounts `~20XX-...`, profanity/prank comments, and rollback markers). Only high-signal candidates are queued for asynchronous Stage-2 LLM verification behind a sliding per-minute call limiter (`LLM_MAX_CALLS_PER_MINUTE=12`), and refined verdicts push to connected browsers via `vandalism_update` WebSocket events.
3. **Distinguishing Vandalism vs. Vandalism Rollbacks**: Anti-vandalism bots like `ClueBot NG` frequently appear in the live stream with edit summaries like `Reverted vandalism (rvv)`. Rather than falsely accusing `ClueBot NG` of vandalism, WikiPulse flags these at `low` severity as **"Vandalism Rollback"** so media monitors still see which pages are actively under attack without false attribution.
4. **In-Memory Rolling Windows**: Using bounded `collections.deque` structures keeps latency low and avoids external database setup for a 6-hour take-home. Every stats method accepts an injectable `now` timestamp for 100% deterministic unit tests.
5. **Mode Switching Resets Rolling Windows**: Switching between `Live` and `Replay` clears `RollingStats` and the Pulse cache and broadcasts a fresh snapshot to all connected clients, preventing live edits from dominating Replay demos (or demo fixtures from polluting Live monitoring).
6. **Non-Edit Event Filtering**: Wikimedia's `recentchange` stream includes `categorize`, `log`, and `external` events. WikiPulse intentionally filters to `type in ("edit", "new")` so EPM and Top 10 reflect actual page edits.

---

## What I Would Do With More Time

- **Go Ingestion Sidecar + Redis Streams / Pub-Sub**: Move SSE ingestion into a small Go service publishing normalized edits to Redis Streams so multiple FastAPI workers can scale horizontally behind a load balancer.
- **Fetch Real Article Diffs for High-Risk Edits**: Wikimedia's SSE event carries metadata (`comment`, `length.old`, `length.new`) but not the actual text diff. Fetching the revision diff via MediaWiki's Compare API (`/w/rest.php/v1/revision/{id}/compare/{other}`) for Stage-2 candidates would dramatically improve LLM vandalism precision (e.g., catching subtle factual hoaxes or libel with small byte deltas).
- **Persistent Time-Series & Incident History**: Store minute aggregates and flagged vandalism incidents in PostgreSQL/TimescaleDB so analysts can inspect 24-hour trends and export incident reports.
- **Observability & CI**: Add Prometheus metrics (`/metrics`), structured JSON logging, and a GitHub Actions workflow running pytest, Vitest, Docker builds, and headless Selenium E2E against Replay mode on every PR.
