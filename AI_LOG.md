# AI Usage Log — WikiPulse

This document records how I used AI coding tools while designing, building, and verifying WikiPulse, including concrete examples of what I kept, what I changed or rejected, where the AI got things wrong, and which parts I chose to handle without AI.

---

## Example 1: Designing the Two-Stage Vandalism Pipeline

- **What I asked**: *"Draft a vandalism detection module for Wikimedia `recentchange` events that highlights suspected vandalism with a short reason while respecting a strict LLM API spending limit."*
- **What I kept**: The two-stage architecture — running fast rule-based heuristics synchronously on every event and only invoking the LLM asynchronously on suspicious candidates behind a sliding per-minute rate limiter (`SlidingRateLimiter`), plus stripping markdown fences before `json.loads`.
- **What I changed / rejected and why**:
  - **Where the AI was wrong**: The initial heuristic treated any edit containing the word `"vandalism"` or `"rvv"` in the edit summary as `high` severity vandalism. On live Wikipedia streams, that immediately mislabeled **ClueBot NG** and human patrollers reverting vandalism as vandals themselves!
  - **How I fixed it**: I split rollback detection into its own branch (`severity="low"`, labeled **"Vandalism Rollback"** in the UI with reason *"Vandalism rollback detected — recent vandalism on this page was just reverted"*) and skipped LLM verification on those events. I also added a benign-deletion check (`redirect`, `merge`, `archive`, `copyvio`, `afd`) so legitimate page redirects aren't flagged as page blanking.
  - I also added support for Wikimedia's newer **temporary accounts** (`~2024-...` / `~2026-...`), which the initial IP-only `is_anonymous_editor` check missed.

---

## Example 2: Frontend TypeScript Build & Vite Environment Types

- **What I asked**: *"Scaffold a Vite + React + TypeScript dashboard with Vitest + React Testing Library configured in `tsconfig.json` and a `useWikiPulse` WebSocket hook that supports overriding the WS URL via `import.meta.env.VITE_WS_URL`."*
- **What I kept**: The `useWikiPulse` hook structure (WebSocket auto-reconnect with exponential backoff, per-client `set_filter` command, Pause/Resume queue merging without duplicate IDs, and `vandalism_update` in-place patching).
- **What I changed / rejected and why**:
  - **Where the AI was wrong**: In `frontend/tsconfig.json`, setting `"types": ["vitest/globals", "@testing-library/jest-dom"]` implicitly excluded Vite's `vite/client` ambient types, causing `tsc --noEmit` and `npm run build` to fail with `TS2339: Property 'env' does not exist on type 'ImportMeta'`.
  - **How I fixed it**: Added `frontend/src/vite-env.d.ts` (`/// <reference types="vite/client" />`) and explicitly included `"vite/client"` in `tsconfig.json` `compilerOptions.types`, then verified `npm run typecheck` and `npm run build` passed cleanly with zero errors.

---

## Example 3: Live vs. Replay Mode Switching State Pollution

- **What I asked**: *"Add a `/api/mode` endpoint and header toggle so a reviewer can switch between live Wikimedia SSE and offline `sample_events.jsonl` replay during a demo."*
- **What I kept**: The runtime task cancellation pattern (`stop_stream_task` -> `start_stream_task`) and header toggle UI with disabled-while-switching state.
- **What I changed / rejected and why**:
  - **Where the AI was wrong**: The first version only restarted the background stream task and pushed a `stats` update. In practice, switching from Live mode to Replay mode left hundreds of Live edits inside `RollingStats` and the cached Pulse summary, so Replay Top 10 and EPM were polluted by Live data for up to 5 minutes (and switching back polluted Live monitoring with synthetic demo fixtures).
  - **How I fixed it**: Added `RollingStats.reset()`, `AIService.clear_cache()`, and `ConnectionManager.iter_clients()`, and updated `switch_mode()` to clear state cleanly and immediately push a fresh `snapshot` to every connected WebSocket client.

---

## Example 4: Backend Port Flexibility & C# Selenium E2E Suite

- **What I asked**: *"Write an end-to-end UI test using Selenium with C# and xUnit that verifies live edit streaming, Top 10 pages, vandalism flags, Pulse summary, and wiki filtering."*
- **What I kept**: Using Selenium 4's built-in Selenium Manager (avoiding brittle manual `chromedriver.exe` downloads), explicit `WebDriverWait` assertions on `data-testid` selectors instead of arbitrary `Thread.Sleep` delays, and headless Chrome configuration.
- **What I changed / rejected and why**:
  - Hardcoding `http://127.0.0.1:8000` in `vite.config.ts` broke local testing when another service was already bound to port `8000`. I made the Vite proxy target configurable via `VITE_BACKEND_URL` (defaulting to `http://127.0.0.1:8000`).
  - I extended the generated C# E2E test to also verify Pause/Resume, switching back to Live Wikimedia SSE mode, and asserting zero `SEVERE` browser console errors via `driver.Manage().Logs.GetLog(LogType.Browser)`.

---

## What I Chose NOT to Use AI For

- **Evaluating live Wikimedia SSE payload quirks**: I inspected raw live events directly from `https://stream.wikimedia.org/v2/stream/recentchange` to verify real field shapes (`meta.id`, `notify_url`, `:ok` heartbeats, `type` values, and `~2026-...` temp accounts) rather than trusting AI's memory of the schema.
- **Defining cost & safety guardrails**: I manually decided the rate-limit defaults (`LLM_MAX_CALLS_PER_MINUTE=12`, `PULSE_REFRESH_MIN_SECONDS=25`), heuristic-first fallback behavior, and test boundary assertions for sliding-window expiration.
- **Final end-to-end verification**: I ran every test suite (`pytest`, `tsc --noEmit`, `vitest`, `vite build`, and `dotnet test` with headless Chrome) against the live running application and audited backend logs for runtime exceptions or silent failures.
