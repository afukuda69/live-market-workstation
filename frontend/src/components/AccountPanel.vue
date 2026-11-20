<script setup>
// Simulated (paper) account for the selected symbol: state, fills, signals.
import { computed } from 'vue'
import { fmtClock, fmtPrice } from '../lib/format'

const props = defineProps({ account: Object, symbol: String, mode: String })
defineEmits(['reset'])

const snap = computed(() => props.account?.snapshot || null)
const money = (v) => (v == null ? '—' : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const signed = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + money(v))
const cls = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '')
const qty = (v) => (v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 6 }))
</script>

<template>
  <div class="panel account" data-testid="account">
    <h2>
      Simulated account · {{ symbol }}
      <span class="badge">SIMULATED</span>
    </h2>
    <template v-if="snap">
      <div class="equity" data-testid="account-equity">
        <span class="big">{{ money(snap.equity) }}</span>
        <span :class="cls(snap.total_return_pct)">{{ signed(snap.total_return_pct) }} %</span>
      </div>
      <table class="kv">
        <tr><td>Cash</td><td data-testid="account-cash">{{ money(snap.cash) }}</td></tr>
        <tr><td>Position</td><td data-testid="account-position">{{ qty(snap.position_qty) }} @ {{ snap.position_qty ? fmtPrice(snap.avg_cost) : '—' }}</td></tr>
        <tr><td>Mark price</td><td>{{ fmtPrice(snap.mark_price) }} <span class="muted" title="Latest observed trade; the current candle may be unfinished">(last tick)</span></td></tr>
        <tr><td>Unrealized P&amp;L</td><td :class="cls(snap.unrealized_pnl)">{{ signed(snap.unrealized_pnl) }}</td></tr>
        <tr><td>Realized P&amp;L</td><td :class="cls(snap.realized_pnl)">{{ signed(snap.realized_pnl) }}</td></tr>
        <tr><td>Fees paid</td><td>{{ money(snap.fees) }}</td></tr>
      </table>
      <div class="small muted">
        Rules: long-only SMA {{ account.params.fast }}/{{ account.params.slow }} crossover on completed candles ·
        market fill at last price · fee {{ account.params.fee_bps / 100 }} % · slippage {{ account.params.slippage_bps / 100 }} %
        · since {{ fmtClock(account.created_ms, true) }}
      </div>

      <h3>Fills</h3>
      <table class="list" data-testid="fills">
        <tr v-if="!account.fills.length"><td class="muted">No trades yet — waiting for a confirmed crossover.</td></tr>
        <tr v-for="f in account.fills.slice(0, 8)" :key="f.order_id">
          <td class="muted">{{ fmtClock(f.time_ms) }}</td>
          <td :class="f.side">{{ f.side.toUpperCase() }}</td>
          <td>{{ qty(f.qty) }} @ {{ fmtPrice(f.price) }}</td>
          <td :class="cls(f.realized_pnl)">{{ f.side === 'sell' ? signed(f.realized_pnl) : '' }}</td>
        </tr>
      </table>

      <h3>Signals</h3>
      <table class="list" data-testid="signals">
        <tr v-if="!account.signals.length"><td class="muted">No confirmed crossovers since the account started.</td></tr>
        <tr v-for="s in account.signals.slice(0, 6)" :key="s.id">
          <td class="muted">{{ fmtClock(s.bucket_start_ms) }}</td>
          <td :class="s.signal === 'up' ? 'buy' : 'sell'">{{ s.signal === 'up' ? '↑ up' : '↓ down' }}</td>
          <td>{{ s.action }}</td>
          <td class="muted note">{{ s.note }}</td>
        </tr>
      </table>
      <div class="row">
        <button class="secondary" @click="$emit('reset')" data-testid="reset-account">Reset account</button>
        <span class="small muted">old account kept in SQLite</span>
      </div>
    </template>
    <div v-else class="small muted">Loading account…</div>
  </div>
</template>

<style scoped>
.panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 12px; }
h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: #8b949e; margin: 0 0 8px; display: flex; gap: 8px; align-items: center; }
h3 { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #8b949e; margin: 10px 0 4px; }
.badge { background: #6e40c9; color: #fff; font-size: 9px; padding: 1px 6px; border-radius: 999px; letter-spacing: .06em; }
.equity { display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px; }
.big { font-size: 20px; font-weight: 700; color: #e6edf3; font-variant-numeric: tabular-nums; }
.kv, .list { width: 100%; font-size: 12px; border-collapse: collapse; }
.kv td { padding: 2px 0; color: #c9d1d9; }
.kv td:last-child { text-align: right; font-variant-numeric: tabular-nums; }
.list td { padding: 2px 4px 2px 0; color: #c9d1d9; font-variant-numeric: tabular-nums; vertical-align: top; }
.note { font-size: 11px; }
.pos { color: #3fb950; } .neg { color: #f85149; }
.buy { color: #58a6ff; font-weight: 700; } .sell { color: #d29922; font-weight: 700; }
.muted { color: #8b949e; }
.small { font-size: 11px; margin: 6px 0; }
.row { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
button { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; border-radius: 6px; padding: 4px 10px; cursor: pointer; font-size: 12px; font-weight: 600; }
</style>
