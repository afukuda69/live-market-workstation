<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import PriceChart from './components/PriceChart.vue'
import StatusBar from './components/StatusBar.vue'
import { createSocket } from './lib/socket'
import { fmtClock } from './lib/format'

const chart = ref(null)
const state = reactive({
  symbols: [],
  symbol: localStorage.getItem('lmw.symbol') || null,
  interval: '1m',
  status: null,
  backend: { state: 'connecting' },
  events: [],
  mode: 'live', // 'live' | 'replay'
  replay: { running: false, speed: 20, clock: null, stats: null, observations: 0, processed: 0 },
  lastCandle: null,
  verify: null,
  verifying: false,
  faultBusy: false,
})
const clockOffset = ref(0) // server time - browser time
const now = ref(Date.now())
let socket = null
let timer = null

function serverNow() {
  return Date.now() + clockOffset.value
}

function applyStatus(st) {
  state.status = st
  if (st?.server_time_ms) clockOffset.value = st.server_time_ms - Date.now()
}

function pushEvent(ev) {
  if (state.events.some((e) => e.id === ev.id)) return
  state.events.unshift(ev)
  state.events.length = Math.min(state.events.length, 60)
}

function subscribe(symbol) {
  state.symbol = symbol
  try { localStorage.setItem('lmw.symbol', symbol) } catch {}
  if (state.mode === 'replay') stopReplay()
  socket.send({ type: 'subscribe', symbol })
}

function onMessage(m) {
  switch (m.type) {
    case 'hello':
      state.symbols = m.symbols
      state.interval = m.interval
      applyStatus(m.status)
      ;(m.events || []).slice().reverse().forEach(pushEvent)
      subscribe(state.symbols.includes(state.symbol) ? state.symbol : state.symbols[0])
      break
    case 'snapshot':
      if (m.symbol !== state.symbol || state.mode !== 'live') return
      applyStatus(m.status)
      chart.value.setCandles(m.candles)
      state.lastCandle = chart.value.lastCandle()
      break
    case 'candle':
      if (state.mode !== 'live' || m.candle.symbol !== state.symbol) return
      chart.value.upsert(m.candle)
      state.lastCandle = chart.value.lastCandle()
      break
    case 'status':
      applyStatus(m.status)
      break
    case 'event':
      pushEvent(m.event)
      break
    case 'replay_started':
      state.replay = { ...state.replay, running: true, clock: m.from_ms, observations: m.observations, processed: 0, stats: null }
      chart.value.clear()
      state.lastCandle = null
      break
    case 'replay_candle':
      if (state.mode !== 'replay') return
      state.replay.clock = m.replay_clock_ms
      state.replay.processed += 1
      chart.value.upsert(m.candle)
      state.lastCandle = chart.value.lastCandle()
      break
    case 'replay_finished':
      state.replay.running = false
      state.replay.stats = m.stats
      break
    case 'error':
      console.warn('backend error:', m.error)
      break
  }
}

function startReplay() {
  state.mode = 'replay'
  state.replay.running = true
  socket.send({ type: 'replay_start', symbol: state.symbol, speed: Number(state.replay.speed) })
}

function stopReplay() {
  socket.send({ type: 'replay_stop' })
  state.replay.running = false
}

function backToLive() {
  stopReplay()
  state.mode = 'live'
  socket.send({ type: 'subscribe', symbol: state.symbol })
}

async function verifyReplay() {
  state.verifying = true
  try {
    const r = await fetch(`/api/replay/verify?symbol=${encodeURIComponent(state.symbol)}`)
    state.verify = await r.json()
  } catch (e) {
    state.verify = { error: String(e) }
  } finally {
    state.verifying = false
  }
}

async function toggleFault() {
  state.faultBusy = true
  try {
    await fetch('/api/feed/fault', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !state.status?.fault_injected }),
    })
  } finally {
    state.faultBusy = false
  }
}

const counters = computed(() => state.status?.counters || {})
const isYahoo = computed(() => state.status?.mode === 'yahoo')

onMounted(() => {
  socket = createSocket({ onMessage, onState: (s) => (state.backend = s) })
  timer = setInterval(() => (now.value = serverNow()), 500)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  socket?.close()
})
</script>

<template>
  <div class="app">
    <header>
      <div class="brand">
        <span class="logo">▮▯</span>
        <div>
          <h1>Live Market Workstation</h1>
          <div class="subtitle">Part 4.1 · market data &amp; dashboard · {{ state.interval }} candles</div>
        </div>
      </div>
      <nav class="symbols" data-testid="symbols">
        <button v-for="s in state.symbols" :key="s" :class="{ active: s === state.symbol }" @click="subscribe(s)">{{ s }}</button>
      </nav>
      <div class="sim-note" title="No orders are placed anywhere">Display only · no trading</div>
    </header>

    <div v-if="state.mode === 'replay'" class="banner replay" data-testid="replay-banner">
      REPLAY — rebuilding {{ state.symbol }} candles from recorded observations in SQLite
      ({{ state.replay.running ? `${state.replay.processed} updates…` : 'finished' }}). Not live data.
      <button @click="backToLive">Back to live</button>
    </div>
    <div v-else-if="state.status && ['unavailable', 'stale'].includes(state.status.state)" class="banner" :class="state.status.state" data-testid="feed-banner">
      <template v-if="state.status.state === 'unavailable'">Live feed unavailable — the chart shows the last stored data and is not updating.</template>
      <template v-else>Feed is connected but quiet — prices may be old (market closed?). Check the data age.</template>
    </div>

    <StatusBar :status="state.status" :symbol="state.symbol" :now="now" :backend="state.backend"
               :mode="state.mode" :replay="state.replay" :last-candle="state.lastCandle" />

    <main>
      <section class="chart-area">
        <PriceChart ref="chart" :mode="state.mode" />
      </section>

      <aside>
        <div class="panel">
          <h2>Replay recorded data</h2>
          <div class="row">
            <label>Speed
              <select v-model="state.replay.speed" :disabled="state.replay.running">
                <option :value="5">5×</option><option :value="20">20×</option>
                <option :value="100">100×</option><option :value="0">instant</option>
              </select>
            </label>
            <button v-if="!state.replay.running" @click="startReplay" data-testid="replay-start">Replay {{ state.symbol }}</button>
            <button v-else @click="stopReplay">Stop</button>
          </div>
          <div v-if="state.replay.stats" class="small">
            Replayed {{ state.replay.stats.observations }} observations → {{ state.replay.stats.candles }} candles
            ({{ state.replay.stats.out_of_order }} out-of-order, {{ state.replay.stats.late }} late).
          </div>
          <div class="row">
            <button class="secondary" @click="verifyReplay" :disabled="state.verifying" data-testid="verify">Verify replay = stored</button>
          </div>
          <div v-if="state.verify" class="small" data-testid="verify-result">
            <template v-if="state.verify.error">Error: {{ state.verify.error }}</template>
            <template v-else>
              <span :class="state.verify.mismatch_count === 0 ? 'ok' : 'bad'">
                {{ state.verify.mismatch_count === 0 ? '✓ match' : `✗ ${state.verify.mismatch_count} mismatches` }}
              </span>
              — {{ state.verify.stored }} stored vs {{ state.verify.replayed }} replayed candles
            </template>
          </div>
        </div>

        <div class="panel">
          <h2>Data quality (this session)</h2>
          <table class="counters" data-testid="counters">
            <tr><td>Ticks processed</td><td>{{ counters.ticks ?? 0 }}</td></tr>
            <tr><td>Duplicates ignored</td><td>{{ counters.duplicates ?? 0 }}</td></tr>
            <tr><td>Out-of-order ticks</td><td>{{ counters.out_of_order ?? 0 }}</td></tr>
            <tr><td>Late ticks (after complete)</td><td>{{ counters.late ?? 0 }}</td></tr>
            <tr><td>Candle revisions</td><td>{{ counters.revisions ?? 0 }}</td></tr>
            <tr><td>Rejected messages</td><td>{{ counters.rejected ?? 0 }}</td></tr>
          </table>
        </div>

        <div class="panel" v-if="isYahoo">
          <h2>Outage test</h2>
          <p class="small">Forces the Yahoo connection down to show the unavailable-feed path. Turning off Wi-Fi tests the same path for real.</p>
          <button :class="state.status?.fault_injected ? '' : 'danger'" :disabled="state.faultBusy" @click="toggleFault" data-testid="fault-toggle">
            {{ state.status?.fault_injected ? 'Restore feed' : 'Simulate feed outage' }}
          </button>
        </div>

        <div class="panel events">
          <h2>Feed events</h2>
          <ul data-testid="events">
            <li v-for="e in state.events" :key="e.id" :class="e.state">
              <span class="t">{{ fmtClock(e.ts_ms) }}</span>
              <span class="s">{{ e.source }} · {{ e.state }}</span>
              <span class="d">{{ e.detail }}</span>
            </li>
          </ul>
        </div>
      </aside>
    </main>
  </div>
</template>

<style scoped>
.app { display: flex; flex-direction: column; gap: 10px; padding: 12px 16px; height: 100vh; box-sizing: border-box; }
header { display: flex; align-items: center; gap: 24px; }
.brand { display: flex; align-items: center; gap: 10px; }
.logo { color: #26a69a; font-size: 22px; letter-spacing: -4px; }
h1 { font-size: 17px; margin: 0; color: #e6edf3; }
.subtitle { font-size: 11px; color: #8b949e; }
.symbols { display: flex; gap: 6px; }
.symbols button { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 5px 12px; border-radius: 6px; cursor: pointer; font-weight: 600; }
.symbols button.active { background: #1f6feb; border-color: #1f6feb; color: #fff; }
.sim-note { margin-left: auto; font-size: 11px; color: #8b949e; border: 1px solid #30363d; padding: 3px 8px; border-radius: 999px; }
.banner { padding: 8px 12px; border-radius: 8px; font-size: 13px; display: flex; gap: 12px; align-items: center; }
.banner.unavailable { background: #3d1214; border: 1px solid #b62324; color: #ffb3ad; }
.banner.stale { background: #3a2a05; border: 1px solid #9a6700; color: #f2cc60; }
.banner.replay { background: #271a45; border: 1px solid #a371f7; color: #d8c4ff; }
main { flex: 1; display: grid; grid-template-columns: 1fr 320px; gap: 10px; min-height: 0; }
.chart-area { min-height: 0; }
aside { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; min-height: 0; }
.panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 12px; }
.panel h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: #8b949e; margin: 0 0 8px; }
.row { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
label { font-size: 12px; color: #c9d1d9; display: flex; gap: 6px; align-items: center; }
select { background: #0d1117; color: #e6edf3; border: 1px solid #30363d; border-radius: 4px; padding: 2px 4px; }
button { background: #238636; color: #fff; border: 0; border-radius: 6px; padding: 5px 10px; cursor: pointer; font-size: 12px; font-weight: 600; }
button.secondary { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; }
button.danger { background: #b62324; }
button:disabled { opacity: .5; cursor: default; }
.banner button { margin-left: auto; background: #6e40c9; }
.small { font-size: 12px; color: #8b949e; margin: 4px 0; }
.ok { color: #3fb950; font-weight: 700; } .bad { color: #f85149; font-weight: 700; }
.counters { width: 100%; font-size: 12px; border-collapse: collapse; }
.counters td { padding: 2px 0; color: #c9d1d9; }
.counters td:last-child { text-align: right; font-variant-numeric: tabular-nums; color: #e6edf3; }
.events { flex: 1; min-height: 160px; display: flex; flex-direction: column; }
.events ul { list-style: none; margin: 0; padding: 0; overflow-y: auto; font-size: 11px; flex: 1; }
.events li { display: grid; grid-template-columns: 62px 1fr; gap: 0 6px; padding: 4px 0; border-bottom: 1px solid #21262d; color: #c9d1d9; }
.events li .t { color: #8b949e; grid-row: span 2; }
.events li .s { font-weight: 600; }
.events li .d { color: #8b949e; word-break: break-word; }
.events li.unavailable .s, .events li.history_unavailable .s, .events li.rejected .s { color: #f85149; }
.events li.live .s, .events li.history_loaded .s { color: #3fb950; }
.events li.stale .s { color: #d29922; }
@media (max-width: 900px) { main { grid-template-columns: 1fr; } .chart-area { height: 420px; } }
</style>
