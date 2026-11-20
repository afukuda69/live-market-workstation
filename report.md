# Assignment 4.2 — Indicators, Simulated Trading, and Backtesting

**Student:** Alessio Fukuda
**Repository:** <https://github.com/afukuda69/live-market-workstation>
**Submitted commit:** `REPLACE_WITH_COMMIT_SHA` (`git rev-parse HEAD`)
**Video (30–60 s):** <https://drive.google.com/file/d/128cJwyx7vLFpiVFsPK5RAOUPNbxrV3Zl/view?usp=sharing>
**CI run (GitHub Actions):** REPLACE_WITH_ACTIONS_RUN_URL

## Summary of what was completed

Built on the Part 4.1 dashboard ([4.1 report](docs/reports/report-4.1.md)):

- **Indicators.** SMA 10 (orange) and SMA 30 (blue) of 1-minute closes are drawn over the live candles, with arrows at confirmed crossovers. They are computed only from **completed** candles. While a candle is still forming, a *provisional* value is shown faded, and a provisional crossover gets a grey "?" marker. Provisional signals are never traded.
- **Simulated trading and accounting.** For each symbol, a long-only SMA-crossover strategy trades a simulated account when a crossover is confirmed: up-cross while flat → buy, down-cross while long → sell all, anything else → recorded as "skip" with the reason. The account panel shows:
  - equity, cash, position and average cost
  - mark price, and unrealized and realized P&L
  - fees
  - recent fills, and signals with the action taken or skipped

  Orders, fills, signals and accounts are stored in SQLite, and the account is rebuilt from its fills after a restart. Execution assumptions (fill price, 0.10 % fee, 0.05 % slippage, sizing, average cost) are listed in [design §11](docs/design.md).
- **Backtesting.** A *Backtest* tab runs the same strategy, indicator and accounting code event by event on historical bars from three sources:
  - a real SPY 1-minute fixture committed to the repo (works offline)
  - this app's stored candles
  - Yahoo history from 1m to 1d, cached in SQLite

  Each decision uses only bars up to the signal bar, and the order fills at the **next bar's open**. The tab shows the chart with SMAs and trades, an equity curve against buy-and-hold, metrics (return, drawdown, round trips, win rate, fees, slippage, time in market, gaps), the round trips, notes on what the result does not establish, and saved earlier runs.

Documents: [design notes §10–15](docs/design.md) · [verification §5–7](docs/verification.md) ·
[evidence log E8–E10](docs/evidence-log.md) · [README](README.md) · [agent instructions](CLAUDE.md) · [AI use](AI-USE.md).

## Key design decisions

1. **One implementation of the rules, two users.** `indicators.py`, `strategy.py` and `accounting.py` contain no I/O. Both the live paper-trading runner and the backtester use them, so whatever the backtest shows is the same logic that trades live.
2. **Only completed candles drive decisions.** A signal from an unfinished candle can disappear (tested), so it is shown as provisional and not traded.
3. **Execution timing that prevents look-ahead.** In the backtest, a signal at bar *i*'s close fills at bar *i+1*'s open. Live, it fills at the latest observed price when the crossover is confirmed. Both rules are documented, and the difference is deliberate.
4. **SMA 10/30 chosen in advance and not tuned**, so the backtest result is not overfit to the sample.
5. **Two SMA implementations, checked against each other.** The batch version is used for snapshots and backtests, and the incremental version runs live. The dashboard can compare them on live data.

## Verification results

- **Trusted reference:** our SMA equals pandas `rolling(n).mean()` on 390 real SPY bars for n = 1, 5, 10, 30 and 50 (relative tolerance 1e-12).
- **Batch vs incremental:** identical on the fixture and after live updates. The **Verify live SMA = batch** button reports ✓ match.
- **Accounting against known examples:**
  - Buy 10 @ 100, then sell 10 @ 110 with a 0.10 % fee gives cash 10,097.90, realized P&L 100.00 and fees 2.10.
  - Slippage, average cost, partial sells and rejections each have their own example.
  - The identity *equity − initial = realized + unrealized − fees* holds on 50 random trade sequences.
- **No look-ahead:** truncating the data at bar *k*, or replacing every later bar with very different prices, leaves all earlier signals and fills unchanged.
- **Test totals:** pytest **92 passed** (52 new). Playwright **8 passed**, 5 consecutive runs. GitHub Actions runs both on every push (link above).
- **A real bug found by the browser test** (evidence log E8): the 4.1 *Verify replay* endpoint marked candles complete without notifying the new live SMA runner, so the live state missed a candle. It was fixed and a regression test added; that test fails on the old code and passes on the new.
- **Backtest result, SMA 10/30, real SPY fixture** (2026-11-12, 390 one-minute bars, no gaps):

  | Costs | Strategy | Buy & hold | Round trips | Max drawdown |
  |---|---|---|---|---|
  | fee 10 bps + slippage 5 bps | **−1.73 %** | −0.22 % | 6 (1 win) | 2.39 % |
  | none | +0.05 % | −0.07 % | 6 | 0.76 % |

  **What this establishes:** on this day the crossover had roughly zero gross edge, and trading costs (about 0.3 % per round trip) made it a loss. **What it leaves uncertain:** other days, instruments, timeframes and windows. Real fills and spreads may differ from the modelled slippage, and the instruments are ones that exist today (survivorship), so a single historical result says nothing about future performance.
- **Live Yahoo run, 2026-11-19, 4:00–8:47 PM ET (macOS)** — details in [verification §7](docs/verification.md):
  - On startup, history backfill loaded 1,261 BTC-USD, 633 AAPL and 694 SPY one-minute bars, so both SMAs were defined immediately. 884 live ticks were then recorded (BTC-USD 258, SPY 479, AAPL 147).
  - **Four confirmed crossovers were handled by the live strategy.** Three down-crosses while flat were recorded as **skip** (SPY 4:00 PM, BTC-USD 4:03 PM, AAPL 4:08 PM). One **AAPL up-cross** on the 4:12 PM candle produced a simulated **buy** of 286.021698 shares at 349.27: the last tick was 349.10, plus 5 bps slippage, plus a 99.90 fee. The decision came 5 s after the candle ended (the grace period), and the price used was 4.1 s old.
  - Real data produced 161 `late` revisions: ticks arriving after their candle was already marked complete. Most were after-hours AAPL/SPY quotes stamped 40–50 s in the past (BTC-USD's average delay was 2.9 s). They updated candles visibly but were never traded, as designed.
  - Unavailable feed observed: the INJECTED outage toggle (4:07 and 4:17 PM), Yahoo keepalive disconnects, then **no network from 4:23 PM** (`gaierror`, retries up to every 30 s) until the connection recovered automatically at about 8:46 PM.

## Remaining limitations

- Long-only market orders and one position per symbol; no stop-loss.
- Gaps in the data (outages, market closures) are bridged by the SMA rather than filled. Stored-candle backtests can therefore be unreliable; the gap count is shown next to every result.
- If delayed data creates a candle behind the newest one, the SMAs are rebuilt, but a crossover that candle would have caused is not traded afterwards.
- The live account marks its position to the last tick, which can be a few seconds old (Yahoo's delay is about 3 s).
- The fixture covers one day of one ETF, so conclusions drawn from it are narrow.
