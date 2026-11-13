# Evidence log (curated, redacted)

Selected entries connecting an intended change, the instruction given to the
coding agent, the resulting files, a test or browser observation, the
decision, and remaining uncertainty. Credentials and personal data are
redacted; this is not a full chat export.

---

## E1 — Initial build of Part 4.1 (agent-generated baseline)

- **Intent:** a working Part 4.1 baseline: live Yahoo → FastAPI → SQLite → Vue chart with replay and feed status.
- **Instruction (excerpt):** pasted the Part 4.1 requirements and the Assignment 4 overview; chose "build the app for you" (Vue + FastAPI + SQLite, yfinance WebSocket streaming, tests).
- **Result:** initial commit — `backend/app/*`, `frontend/src/*`, tests, docs.
- **Check:** `pytest` 40 passed; Playwright 4 passed against the SIMULATED feed; screenshots of live (mock) and replay views.
- **Decision:** accepted as a baseline to study and extend; every module must be explainable before submission.
- **Uncertainty:** not yet run against the real Yahoo streamer (sandbox network blocks it) — see verification §3.

## E2 — Revised approach: yfinance's `listen()` hides outages

- **Intent:** show an unavailable-feed condition in the UI.
- **First approach:** use `yfinance.AsyncWebSocket.subscribe()` + `listen(handler)`.
- **Evidence:** reading `yfinance/live.py` (v1.8.0): inside `listen()`, any exception other than cancellation is logged, followed by `await asyncio.sleep(3)` and `await self._connect()` — the reconnect loop is internal, so the caller is never told the connection dropped. In addition, `_connect()` only reconnects when `self._ws is None`, so the stale socket can be reused.
- **Decision:** wrote `feeds/yahoo_live.py`: same protocol (subscribe JSON, 15 s re-subscribe heartbeat) and the same protobuf schema (`yfinance.pricing_pb2.PricingData`), but our own connect/reconnect loop that reports `connecting → live → unavailable` with exponential backoff, plus a fault toggle for demos.
- **Generated code (excerpt):**
  ```python
  except Exception as e:  # network errors, DNS, TLS, closed socket, injected fault
      attempts += 1
      delay = min(self.backoff_cap, 2 ** min(attempts, 5))
      await svc.set_state("unavailable", f"{type(e).__name__}: {e} — retry #{attempts} in {delay:.0f}s", self.source)
      await self._sleep_or_wake(delay)
  ```
- **Check:** `test_unreachable_server_reports_unavailable_and_retries`, `test_server_closing_connection_is_an_outage`, `test_injected_fault_and_recovery`; plus the real 403 block recorded in verification §2.
- **Uncertainty:** depends on yfinance's internal protobuf module path; if yfinance moves it, `normalize.py` imports must change.

## E3 — Failure: replay verification raced the finalisation loop

- **Intent:** `/api/replay/verify` should report 0 mismatches when replay equals stored data.
- **Observation:** `test_rest_retrieval_revisions_and_verify` failed: `mismatch_count == 1`.
- **Diagnosis:** the test candles were from 2025, so replay (clock = now) marked them complete, while the stored row was still provisional because the 1-second housekeeping loop had not run yet. Both were "right" at different clocks.
- **Decision:** the verify endpoint now finalises due candles at a single clock and passes the same clock to the rebuild; API tests use a long grace period so the background loop cannot change candles mid-test. Documented in design §6 that revision numbers may differ by one in that ≤1 s window while OHLCV/completeness match.
- **Check:** suite re-run 3× → 38/38, then 40/40 after adding tests.

## E4 — Test expectation corrected: a late tick can move the close

- **Observation:** my first `test_out_of_order_and_late_are_recorded` expected close 101.0 after a late tick, but the late tick (source time :59) is the *newest* tick in that minute, so the rule "close = latest source time" makes it 97.0.
- **Decision:** kept the rule (it matches how a candle is defined) and fixed the expectation.

## E5 — Failure on macOS: SSL certificate verification

- **Observation:** first live start on the Mac logged `feed yahoo-ws: unavailable SSLCertVerificationError: certificate verify failed: unable to get local issuer certificate`, retrying with backoff; the dashboard showed UNAVAILABLE.
- **Diagnosis:** python.org's Python 3.14 for macOS ships without a CA certificate bundle, so every TLS connection failed. Not an app bug — and a useful real check that the outage path works.
- **Decision:** ran `/Applications/Python 3.14/Install Certificates.command` and restarted; the feed went LIVE. Fallback documented: `export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")`.
- **Uncertainty:** other machines with the same Python build will hit this; README could mention it.

## E6 — Revised approach: GitHub push rejected for the CI workflow

- **Observation:** `git push` failed: `refusing to allow a Personal Access Token to create or update workflow .github/workflows/ci.yml without workflow scope`; a later attempt returned 403.
- **Evidence:** `curl -sI -H "Authorization: token …" https://api.github.com/user | grep -i x-oauth-scopes` first printed an empty scope list, showing the token in use had no usable scopes.
- **Decision:** created a classic token with `repo` + `workflow`, verified it printed `x-oauth-scopes: repo, workflow`, cleared the cached credential (`git credential-osxkeychain erase`) and pushed successfully (commit `8fe748b`). The token was then regenerated because it had been pasted into a chat.

## E7 — Live observation: Yahoo keepalive disconnects

- **Observation:** `feed_events` shows 7 `ConnectionClosedError: keepalive ping timeout` disconnects between 8:35 and 9:43 PM, each followed by reconnect in ~2–5 s, often after a quiet period flagged STALE.
- **Decision:** behaviour is handled correctly (visible, logged, recovered), so kept for 4.1 and listed as a limitation; investigate WebSocket `ping_interval`/`ping_timeout` settings in 4.2.

---

### Template for new entries

- **Intent:**
- **Instruction (excerpt):**
- **Result (file / diff / commit):**
- **Check (test or browser observation):**
- **Decision:**
- **Uncertainty:**
