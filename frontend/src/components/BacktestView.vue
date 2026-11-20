<script setup>
// Backtest of the SMA crossover strategy on historical bars.
import { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import { createChart } from 'lightweight-charts'
import PriceChart from './PriceChart.vue'
import { fmtClock, fmtPrice } from '../lib/format'

const props = defineProps({ symbols: Array, active: Boolean })

const form = reactive({
  source: 'fixture', symbol: 'SPY', interval: '5m', fixture: '', refresh: true,
  fast: 10, slow: 30, initial_cash: 100000, fee_bps: 10, slippage_bps: 5, allocation: 1,
})
const options = ref(null)
const result = ref(null)
const running = ref(false)
const error = ref('')
const runs = ref([])
const priceChart = ref(null)
const equityEl = ref(null)
const eqChart = shallowRef(null)
let eqSeries = null
let bhSeries = null

const money = (v) => (v == null ? '—' : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const pct = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2) + ' %')
const cls = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '')
const dateRange = (d) => d?.start_ms ? `${fmtClock(d.start_ms, true)} → ${fmtClock(d.end_ms, true)}` : '—'

async function loadOptions() {
  const r = await fetch('/api/backtest/options')
  options.value = await r.json()
  if (!form.fixture && options.value.fixtures.length) form.fixture = options.value.fixtures[0]
  await loadRuns()
}

async function loadRuns() {
  const r = await fetch('/api/backtest/runs?limit=10')
  runs.value = (await r.json()).runs
}

async function run() {
  running.value = true
  error.value = ''
  try {
    const r = await fetch('/api/backtest', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...form, fast: +form.fast, slow: +form.slow, initial_cash: +form.initial_cash,
        fee_bps: +form.fee_bps, slippage_bps: +form.slippage_bps, allocation: +form.allocation }),
    })
    const d = await r.json()
    if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail))
    show(d)
    await loadRuns()
  } catch (e) {
    error.value = String(e.message || e)
  } finally {
    running.value = false
  }
}

async function openRun(id) {
  const r = await fetch(`/api/backtest/runs/${id}`)
  show(await r.json())
}

async function show(d) {
  result.value = d
  await nextTick()
  const step = intervalSeconds.value
  priceChart.value.clear()
  priceChart.value.setCandles(d.ohlc.map(([t, o, h, l, c, v]) => ({ time: Math.floor(t / 1000), open: o, high: h, low: l, close: c, volume: v })))
  const sigByTime = new Map(d.signals.map((s) => [s.time_ms, s.signal]))
  priceChart.value.setIndicators(d.indicators.map((p) => ({ time: Math.floor(p.time_ms / 1000), fast: p.fast, slow: p.slow,
    signal: sigByTime.get(p.time_ms) || null, provisional: false })))
  priceChart.value.setTrades(d.trades)
  priceChart.value.fit()
  drawEquity(d)
}

function drawEquity(d) {
  if (!eqChart.value) return
  eqSeries.setData(d.equity.map((e) => ({ time: Math.floor(e.time_ms / 1000), value: e.equity })))
  bhSeries.setData(d.equity.filter((e) => e.buy_hold_equity != null)
    .map((e) => ({ time: Math.floor(e.time_ms / 1000), value: e.buy_hold_equity })))
  setTimeout(() => eqChart.value.timeScale().fitContent(), 60)
}

const intervalSeconds = computed(() => ({ '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '1d': 86400 }[result.value?.data?.interval] || 60))
const m = computed(() => result.value?.metrics || {})

onMounted(() => {
  eqChart.value = createChart(equityEl.value, {
    autoSize: true,
    layout: { background: { color: '#0d1117' }, textColor: '#9aa4b2' },
    grid: { vertLines: { color: '#161b22' }, horzLines: { color: '#161b22' } },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d', timeVisible: true },
    localization: { timeFormatter: (t) => new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) },
  })
  eqChart.value.timeScale().applyOptions({
    tickMarkFormatter: (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  })
  eqSeries = eqChart.value.addLineSeries({ color: '#a371f7', lineWidth: 2, title: 'strategy' })
  bhSeries = eqChart.value.addLineSeries({ color: '#8b949e', lineWidth: 1, lineStyle: 2, title: 'buy & hold' })
})
onBeforeUnmount(() => eqChart.value?.remove())
watch(() => props.active, (a) => { if (a && !options.value) loadOptions() }, { immediate: true })
</script>

<template>
  <div class="bt" data-testid="backtest-view">
    <section class="form panel">
      <h2>Backtest · SMA crossover (long-only)</h2>
      <div class="grid">
        <label>Data
          <select v-model="form.source" data-testid="bt-source">
            <option value="fixture">Fixture (real SPY 1m, in repo — offline)</option>
            <option value="stored">Stored live candles (this app's SQLite)</option>
            <option value="yahoo">Yahoo history (download + cache)</option>
          </select>
        </label>
        <label v-if="form.source === 'fixture'">Fixture
          <select v-model="form.fixture"><option v-for="f in options?.fixtures || []" :key="f" :value="f">{{ f }}</option></select>
        </label>
        <label v-if="form.source !== 'fixture'">Symbol
          <select v-model="form.symbol"><option v-for="s in symbols" :key="s" :value="s">{{ s }}</option></select>
        </label>
        <label v-if="form.source === 'yahoo'">Interval
          <select v-model="form.interval">
            <option v-for="(period, iv) in options?.yahoo_intervals || {}" :key="iv" :value="iv">{{ iv }} (last {{ period }})</option>
          </select>
        </label>
        <label v-if="form.source === 'yahoo'" class="check"><input type="checkbox" v-model="form.refresh" /> download now (else cache only)</label>
        <label>Fast SMA <input type="number" min="1" v-model="form.fast" data-testid="bt-fast" /></label>
        <label>Slow SMA <input type="number" min="2" v-model="form.slow" data-testid="bt-slow" /></label>
        <label>Start cash <input type="number" v-model="form.initial_cash" /></label>
        <label>Fee (bps) <input type="number" step="0.5" v-model="form.fee_bps" /></label>
        <label>Slippage (bps) <input type="number" step="0.5" v-model="form.slippage_bps" /></label>
      </div>
      <div class="row">
        <button @click="run" :disabled="running" data-testid="bt-run">{{ running ? 'Running…' : 'Run backtest' }}</button>
        <span v-if="error" class="err" data-testid="bt-error">{{ error }}</span>
      </div>
      <p class="small muted">
        Decisions use only completed bars up to the signal bar; orders fill at the <b>next bar's open</b> with fee and slippage.
        Results describe this sample only — they do not predict future performance.
      </p>
    </section>

    <div class="results" v-show="result">
      <section class="metrics panel" data-testid="bt-metrics">
        <div class="metric"><div class="k">Strategy return</div><div class="v" :class="cls(m.total_return_pct)" data-testid="bt-return">{{ pct(m.total_return_pct) }}</div></div>
        <div class="metric"><div class="k">Buy &amp; hold</div><div class="v" :class="cls(m.buy_and_hold_return_pct)">{{ pct(m.buy_and_hold_return_pct) }}</div></div>
        <div class="metric"><div class="k">Final equity</div><div class="v">{{ money(m.equity) }}</div></div>
        <div class="metric"><div class="k">Max drawdown</div><div class="v neg">{{ m.max_drawdown_pct != null ? '−' + m.max_drawdown_pct.toFixed(2) + ' %' : '—' }}</div></div>
        <div class="metric"><div class="k">Round trips</div><div class="v" data-testid="bt-trips">{{ m.round_trips }}</div></div>
        <div class="metric"><div class="k">Win rate</div><div class="v">{{ m.win_rate_pct == null ? '—' : m.win_rate_pct.toFixed(0) + ' %' }}</div></div>
        <div class="metric"><div class="k">Fees paid</div><div class="v">{{ money(m.fees) }}</div></div>
        <div class="metric"><div class="k">Slippage cost</div><div class="v">{{ money(m.slippage_cost) }}</div></div>
        <div class="metric"><div class="k">Time in market</div><div class="v">{{ m.exposure_pct?.toFixed(0) }} %</div></div>
        <div class="data small muted">
          {{ result?.data?.symbol }} · {{ result?.data?.interval }} · {{ result?.data?.bars }} bars ·
          {{ dateRange(result?.data) }} · source: {{ result?.data?.source }}{{ result?.data?.fixture ? ' (' + result.data.fixture + ')' : '' }}
          · gaps: {{ result?.data?.gaps }}
          <template v-if="result?.data?.fetch"> · downloaded {{ result.data.fetch.ok ? fmtClock(result.data.fetch.fetched_ms, true) : 'FAILED' }}</template>
          · run #{{ result?.id }}
        </div>
      </section>

      <section class="charts">
        <div class="price"><PriceChart ref="priceChart" :sma="{ fast: result?.params?.fast ?? form.fast, slow: result?.params?.slow ?? form.slow }"
                                       :interval-seconds="intervalSeconds" :min-height="320" :show-legend-state="false" /></div>
        <div class="equity panel">
          <div class="legend small"><span class="eq">━ strategy equity</span> <span class="bh">┅ buy &amp; hold</span></div>
          <div ref="equityEl" class="eqchart" data-testid="bt-equity"></div>
        </div>
      </section>

      <section class="lower">
        <div class="panel">
          <h2>Round trips</h2>
          <table class="list" data-testid="bt-trades">
            <tr><th>Entry</th><th>Exit</th><th>Qty</th><th>Entry px</th><th>Exit px</th><th>P&amp;L (net)</th><th>Return</th></tr>
            <tr v-for="(r, i) in result?.round_trips || []" :key="i">
              <td>{{ fmtClock(r.entry_ms, true) }}</td><td>{{ fmtClock(r.exit_ms, true) }}</td>
              <td>{{ r.qty.toFixed(4) }}</td><td>{{ fmtPrice(r.entry_price) }}</td><td>{{ fmtPrice(r.exit_price) }}</td>
              <td :class="cls(r.pnl)">{{ money(r.pnl) }}</td><td :class="cls(r.return_pct)">{{ pct(r.return_pct) }}</td>
            </tr>
            <tr v-if="!(result?.round_trips || []).length"><td colspan="7" class="muted">No completed round trips.</td></tr>
          </table>
        </div>
        <div class="panel">
          <h2>What this result does and does not show</h2>
          <ul class="notes" data-testid="bt-notes">
            <li v-for="(n, i) in result?.notes || []" :key="'n' + i" class="warn">{{ n }}</li>
            <li>No look-ahead: each decision uses bars up to its signal bar; execution uses the next bar's open (tested by truncating future data).</li>
            <li>One instrument, one period. Instruments chosen today exist today (survivorship), so this says nothing about strategies across delisted assets.</li>
            <li>SMA windows were fixed in advance, not tuned on this data. Trying many windows and keeping the best would overfit.</li>
            <li>Fills assume the next open is available at that price plus slippage; real fills, spreads and outages can differ.</li>
          </ul>
          <h2>Previous runs</h2>
          <table class="list" data-testid="bt-runs">
            <tr v-for="r in runs" :key="r.id" class="clickable" @click="openRun(r.id)">
              <td>#{{ r.id }}</td><td>{{ fmtClock(r.created_ms, true) }}</td><td>{{ r.symbol }} {{ r.interval }} · {{ r.source }}</td>
              <td>SMA {{ r.params.fast }}/{{ r.params.slow }}</td><td :class="cls(r.metrics.total_return_pct)">{{ pct(r.metrics.total_return_pct) }}</td>
            </tr>
          </table>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.bt { flex: 1; display: flex; flex-direction: column; gap: 10px; min-height: 0; overflow-y: auto; }
.panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 12px; }
h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: #8b949e; margin: 0 0 8px; }
.grid { display: flex; flex-wrap: wrap; gap: 8px 16px; }
label { font-size: 12px; color: #c9d1d9; display: flex; flex-direction: column; gap: 3px; }
label.check { flex-direction: row; align-items: center; align-self: flex-end; }
input, select { background: #0d1117; color: #e6edf3; border: 1px solid #30363d; border-radius: 4px; padding: 4px 6px; font-size: 12px; }
input[type=number] { width: 90px; }
.row { display: flex; gap: 10px; align-items: center; margin-top: 10px; }
button { background: #238636; color: #fff; border: 0; border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 12px; font-weight: 600; }
button:disabled { opacity: .5; }
.err { color: #f85149; font-size: 12px; }
.small { font-size: 12px; } .muted { color: #8b949e; }
.metrics { display: flex; flex-wrap: wrap; gap: 8px 26px; align-items: flex-end; }
.metric .k { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: #8b949e; }
.metric .v { font-size: 18px; font-weight: 700; color: #e6edf3; font-variant-numeric: tabular-nums; }
.data { flex-basis: 100%; }
.pos { color: #3fb950 !important; } .neg { color: #f85149 !important; }
.charts { display: grid; grid-template-columns: 2fr 1fr; gap: 10px; }
.price { height: 340px; }
.equity { display: flex; flex-direction: column; height: 340px; }
.eqchart { flex: 1; position: relative; }
.legend .eq { color: #a371f7; } .legend .bh { color: #8b949e; }
.lower { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.list { width: 100%; font-size: 12px; border-collapse: collapse; }
.list th { text-align: left; color: #8b949e; font-weight: 600; padding: 2px 4px; }
.list td { padding: 3px 4px; border-top: 1px solid #21262d; color: #c9d1d9; font-variant-numeric: tabular-nums; }
.clickable { cursor: pointer; } .clickable:hover td { background: #1c2128; }
.notes { margin: 0 0 12px; padding-left: 18px; font-size: 12px; color: #c9d1d9; }
.notes li { margin-bottom: 4px; } .notes li.warn { color: #d29922; }
@media (max-width: 1100px) { .charts, .lower { grid-template-columns: 1fr; } }
</style>
