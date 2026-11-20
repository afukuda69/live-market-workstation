# 60-second demo script (Part 4.2 video)

Before recording, let the backend run on BTC-USD for 45+ minutes (SMA 30 needs 30 completed 1-minute candles, and crossovers need time). For a guaranteed quick demo, use the simulated feed with 5-second candles instead (README → "Part 4.2 features"), and say on camera that it is SIMULATED data.

| Time | Show | Say |
|---|---|---|
| 0–12 s | Live tab: candles with orange SMA 10 / blue SMA 30, arrows at crossovers, B/S markers | "Two simple moving averages from completed candles; arrows mark crossovers, B and S are simulated trades." |
| 12–20 s | Hover the newest candle: faded SMA end, PROVISIONAL tag, maybe a grey "?" | "Values from the unfinished candle are provisional and never traded." |
| 20–30 s | Account panel: equity, cash, position, unrealized/realized P&L, fills, signals with "skip" reasons | "The strategy trades a simulated account: up-cross while flat buys, down-cross while long sells; fees and slippage included." |
| 30–35 s | Click **Verify live SMA = batch** → ✓ match | "The live incremental SMA matches a full recomputation." |
| 35–55 s | Backtest tab → Run (SPY fixture): metrics, chart with trades, equity vs buy-and-hold, notes | "Backtest on real SPY data: each decision only sees bars up to the signal and fills at the next open. With costs it lost 1.7 % versus −0.2 % buy-and-hold." |
| 55–60 s | Notes panel | "Design decision: one strategy and accounting module shared by live trading and the backtest." |
