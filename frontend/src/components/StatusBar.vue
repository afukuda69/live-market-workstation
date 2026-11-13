<script setup>
// Source, feed state, data age and latency for the selected symbol.
// Data age = (server clock now) - (source timestamp of the newest tick):
// it measures how old the market data is, not how recently the page redrew.
import { computed } from 'vue'
import { ageLevel, fmtAge, fmtClock, fmtPrice } from '../lib/format'

const props = defineProps({
  status: Object,
  symbol: String,
  now: Number, // server-aligned clock (ms)
  backend: Object, // browser <-> backend socket state
  mode: String, // 'live' | 'replay'
  replay: Object,
  lastCandle: Object,
})

const sym = computed(() => props.status?.symbols?.[props.symbol] || {})
const dataAgeMs = computed(() => (sym.value.last_source_ms ? props.now - sym.value.last_source_ms : null))
const latencyMs = computed(() =>
  sym.value.last_received_ms && sym.value.last_source_ms ? sym.value.last_received_ms - sym.value.last_source_ms : null)
const feedState = computed(() => {
  if (props.backend?.state !== 'open') return 'backend-down'
  return props.status?.state || 'unknown'
})
const feedLabel = computed(() => ({
  live: props.status?.mode === 'mock' ? 'SIMULATED' : 'LIVE',
  stale: 'STALE', unavailable: 'UNAVAILABLE', connecting: 'CONNECTING', starting: 'STARTING',
  stopped: 'NO FEED', 'backend-down': 'BACKEND OFFLINE', unknown: '…',
}[feedState.value] || feedState.value.toUpperCase()))
</script>

<template>
  <div class="statusbar" data-testid="statusbar">
    <div class="cell">
      <div class="k">Source</div>
      <div class="v" data-testid="source-label">{{ status?.source_label || '—' }}</div>
    </div>
    <div class="cell">
      <div class="k">Feed</div>
      <div class="v">
        <span class="pill" :class="[feedState, status?.mode]" data-testid="feed-state">{{ feedLabel }}</span>
        <span v-if="status?.fault_injected" class="pill injected" title="Outage triggered by the demo toggle">INJECTED</span>
      </div>
    </div>
    <div class="cell" v-if="mode !== 'replay'">
      <div class="k">Data age ({{ symbol }})</div>
      <div class="v" :class="'age-' + ageLevel(dataAgeMs)" data-testid="data-age">{{ fmtAge(dataAgeMs) }}</div>
      <div class="sub">last tick {{ fmtClock(sym.last_source_ms, true) }}</div>
    </div>
    <div class="cell" v-else>
      <div class="k">Replay clock</div>
      <div class="v age-replay" data-testid="replay-clock">{{ fmtClock(replay?.clock, true) }}</div>
      <div class="sub">recorded data, not live</div>
    </div>
    <div class="cell">
      <div class="k">Last price</div>
      <div class="v mono" data-testid="last-price">{{ fmtPrice(mode === 'replay' ? lastCandle?.close : sym.last_price) }}</div>
    </div>
    <div class="cell" v-if="mode !== 'replay'">
      <div class="k">Source → backend</div>
      <div class="v mono">{{ latencyMs == null ? '—' : latencyMs + ' ms' }}</div>
    </div>
    <div class="cell">
      <div class="k">Current candle</div>
      <div class="v">
        <span v-if="lastCandle" class="tag" :class="lastCandle.is_complete ? 'complete' : 'provisional'">
          {{ lastCandle.is_complete ? 'COMPLETE' : 'PROVISIONAL' }}
        </span>
        <span v-else>—</span>
        <span v-if="lastCandle" class="sub"> rev {{ lastCandle.revision }}</span>
      </div>
    </div>
    <div class="detail" data-testid="feed-detail">
      <template v-if="feedState === 'backend-down'">
        Lost connection to the backend — retrying{{ backend?.retryInMs ? ` in ${Math.round(backend.retryInMs / 1000)} s` : '' }}. Data shown is frozen.
      </template>
      <template v-else>{{ status?.detail }}</template>
    </div>
  </div>
</template>

<style scoped>
.statusbar { display: flex; flex-wrap: wrap; gap: 6px 22px; align-items: flex-start; padding: 10px 14px;
  background: #161b22; border: 1px solid #30363d; border-radius: 8px; }
.cell { min-width: 110px; }
.k { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: #8b949e; }
.v { font-size: 14px; color: #e6edf3; margin-top: 2px; display: flex; gap: 6px; align-items: center; }
.sub { font-size: 11px; color: #8b949e; }
.mono { font-variant-numeric: tabular-nums; }
.detail { flex-basis: 100%; font-size: 12px; color: #8b949e; border-top: 1px solid #21262d; padding-top: 6px; }
.pill { font-size: 11px; font-weight: 700; letter-spacing: .05em; padding: 2px 8px; border-radius: 999px; background: #30363d; color: #c9d1d9; }
.pill.live { background: #1a7f37; color: #fff; }
.pill.live.mock { background: #6e40c9; }
.pill.stale { background: #9a6700; color: #fff; }
.pill.unavailable, .pill.backend-down { background: #b62324; color: #fff; }
.pill.connecting, .pill.starting { background: #1f6feb; color: #fff; }
.pill.injected { background: transparent; border: 1px solid #f85149; color: #f85149; }
.age-good { color: #3fb950; } .age-warn { color: #d29922; } .age-bad { color: #f85149; } .age-replay { color: #a371f7; }
.tag { font-size: 10px; font-weight: 700; letter-spacing: .04em; padding: 1px 6px; border-radius: 4px; }
.tag.complete { background: #1f6f43; color: #d2f8e0; }
.tag.provisional { background: #5a4a12; color: #ffe8a3; border: 1px dashed #d4a72c; }
</style>
