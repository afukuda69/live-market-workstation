<script setup>
// Candlesticks + volume + two SMAs with TradingView Lightweight Charts (v4).
// Provisional (incomplete) candles are drawn hollow; SMA values that include
// an unfinished candle are drawn faded; provisional crossovers get a hollow
// "?" marker and are never traded. Confirmed crossovers get arrows, and
// simulated fills get B/S markers.
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { createChart, CrosshairMode } from 'lightweight-charts'
import { fmtPrice, fmtVolume } from '../lib/format'

const props = defineProps({
  mode: { type: String, default: 'live' },
  sma: { type: Object, default: () => ({ fast: 10, slow: 30 }) },
  intervalSeconds: { type: Number, default: 60 },
  minHeight: { type: Number, default: 380 },
  showLegendState: { type: Boolean, default: true },
})

const el = ref(null)
const legend = ref(null)
const chart = shallowRef(null)
let candleSeries = null
let volumeSeries = null
let fastSeries = null
let slowSeries = null
const bars = new Map() // time -> wire candle
const ind = new Map() // time -> indicator point
let trades = [] // [{time_ms, side, price, qty}]

const UP = '#26a69a'
const DOWN = '#ef5350'
const FAST_COLOR = '#f0b429'
const SLOW_COLOR = '#58a6ff'
const FADED = { [FAST_COLOR]: 'rgba(240,180,41,0.4)', [SLOW_COLOR]: 'rgba(88,166,255,0.4)' }

function toCandle(c) {
  const up = c.close >= c.open
  const base = up ? UP : DOWN
  if (c.is_complete !== false) return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }
  return {
    time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
    color: up ? 'rgba(38,166,154,0.15)' : 'rgba(239,83,80,0.15)',
    borderColor: base, wickColor: base,
  }
}

function toVolume(c) {
  const up = c.close >= c.open
  const alpha = c.is_complete !== false ? 0.55 : 0.25
  return { time: c.time, value: c.volume, color: up ? `rgba(38,166,154,${alpha})` : `rgba(239,83,80,${alpha})` }
}

function lineData(key, color) {
  return [...ind.values()].sort((a, b) => a.time - b.time).map((p) =>
    p[key] == null ? { time: p.time } : { time: p.time, value: p[key], color: p.provisional ? FADED[color] : color })
}

function markers() {
  const ms = []
  for (const p of ind.values()) {
    if (!p.signal) continue
    if (p.provisional) {
      ms.push({ time: p.time, position: p.signal === 'up' ? 'belowBar' : 'aboveBar', shape: 'circle',
        color: '#8b949e', text: p.signal === 'up' ? 'up?' : 'down?' })
    } else {
      ms.push({ time: p.time, position: p.signal === 'up' ? 'belowBar' : 'aboveBar',
        shape: p.signal === 'up' ? 'arrowUp' : 'arrowDown', color: p.signal === 'up' ? '#3fb950' : '#f85149',
        text: '' })
    }
  }
  const step = props.intervalSeconds
  for (const t of trades) {
    const time = Math.floor(t.time_ms / 1000 / step) * step
    if (!bars.has(time)) continue
    ms.push({ time, position: t.side === 'buy' ? 'belowBar' : 'aboveBar', shape: 'square',
      color: t.side === 'buy' ? '#1f6feb' : '#d29922', text: t.side === 'buy' ? 'B' : 'S' })
  }
  return ms.sort((a, b) => a.time - b.time)
}

function redrawAll() {
  const sorted = [...bars.values()].sort((a, b) => a.time - b.time)
  candleSeries.setData(sorted.map(toCandle))
  volumeSeries.setData(sorted.map(toVolume))
  redrawIndicators()
}

function redrawIndicators() {
  fastSeries.setData(lineData('fast', FAST_COLOR))
  slowSeries.setData(lineData('slow', SLOW_COLOR))
  candleSeries.setMarkers(markers())
}

function setCandles(list) {
  bars.clear()
  for (const c of list) bars.set(c.time, c)
  redrawAll()
  chart.value.timeScale().scrollToRealTime()
  showLegend(null)
}

function upsert(c) {
  const lastTime = bars.size ? Math.max(...bars.keys()) : -Infinity
  bars.set(c.time, c)
  if (c.time >= lastTime) {
    candleSeries.update(toCandle(c))
    volumeSeries.update(toVolume(c))
  } else {
    redrawAll() // revision of an older candle: the library can only update the last bar
  }
  showLegend(null)
}

function setIndicators(points) {
  ind.clear()
  for (const p of points) ind.set(p.time, p)
  redrawIndicators()
  showLegend(null)
}

function upsertIndicator(p) {
  const prev = ind.get(p.time)
  ind.set(p.time, p)
  const lastTime = Math.max(...ind.keys())
  const signalChanged = (prev?.signal || null) !== (p.signal || null) || (prev?.provisional !== p.provisional && p.signal)
  if (p.time >= lastTime && !signalChanged) {
    const f = lineData('fast', FAST_COLOR).at(-1)
    const s = lineData('slow', SLOW_COLOR).at(-1)
    fastSeries.update(f)
    slowSeries.update(s)
  } else {
    redrawIndicators()
  }
  // a provisional point for an older time is stale once a newer confirmed one exists
  showLegend(null)
}

function setTrades(list) {
  trades = list || []
  candleSeries.setMarkers(markers())
}

function clear() {
  bars.clear()
  ind.clear()
  trades = []
  candleSeries.setData([])
  volumeSeries.setData([])
  fastSeries.setData([])
  slowSeries.setData([])
  candleSeries.setMarkers([])
  showLegend(null)
}

function lastCandle() {
  if (!bars.size) return null
  return bars.get(Math.max(...bars.keys()))
}

function latestIndicator() {
  if (!ind.size) return null
  return ind.get(Math.max(...ind.keys()))
}

function showLegend(time) {
  const c = time != null ? bars.get(time) : lastCandle()
  // hovering: that candle's values; otherwise the newest indicator values
  // (the newest candle has none for a moment while the previous one completes)
  const p = time != null ? ind.get(c?.time) : (ind.get(c?.time) || latestIndicator())
  legend.value = c ? { ...c, ind: p || null } : null
}

defineExpose({ setCandles, upsert, clear, lastCandle, setIndicators, upsertIndicator, setTrades,
  fit: () => setTimeout(() => chart.value?.timeScale().fitContent(), 60) })

onMounted(() => {
  chart.value = createChart(el.value, {
    autoSize: true,
    layout: { background: { color: '#0d1117' }, textColor: '#9aa4b2', fontFamily: 'Inter, system-ui, sans-serif' },
    grid: { vertLines: { color: '#161b22' }, horzLines: { color: '#161b22' } },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: props.intervalSeconds < 60, rightOffset: 6 },
    localization: {
      timeFormatter: (t) => new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
    },
  })
  chart.value.timeScale().applyOptions({
    tickMarkFormatter: (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  })
  candleSeries = chart.value.addCandlestickSeries({
    upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN,
  })
  candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.26 } })
  fastSeries = chart.value.addLineSeries({ color: FAST_COLOR, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
  slowSeries = chart.value.addLineSeries({ color: SLOW_COLOR, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
  volumeSeries = chart.value.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' })
  chart.value.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
  chart.value.subscribeCrosshairMove((p) => showLegend(p?.time ?? null))
})

onBeforeUnmount(() => chart.value?.remove())
</script>

<template>
  <div class="chart-wrap" :class="{ replay: props.mode === 'replay' }" :style="{ minHeight: props.minHeight + 'px' }">
    <div ref="el" class="chart" data-testid="chart"></div>
    <div v-if="legend" class="legend" data-testid="legend">
      <span class="lg-time">{{ new Date(legend.time * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }}</span>
      <span>O <b>{{ fmtPrice(legend.open) }}</b></span>
      <span>H <b>{{ fmtPrice(legend.high) }}</b></span>
      <span>L <b>{{ fmtPrice(legend.low) }}</b></span>
      <span>C <b :class="legend.close >= legend.open ? 'up' : 'down'">{{ fmtPrice(legend.close) }}</b></span>
      <span v-if="legend.volume != null">Vol <b>{{ fmtVolume(legend.volume) }}</b></span>
      <span class="sma-fast" data-testid="sma-fast">SMA {{ sma.fast }} <b>{{ fmtPrice(legend.ind?.fast) }}</b></span>
      <span class="sma-slow" data-testid="sma-slow">SMA {{ sma.slow }} <b>{{ fmtPrice(legend.ind?.slow) }}</b></span>
      <template v-if="showLegendState && legend.is_complete !== undefined">
        <span class="tag" :class="legend.is_complete ? 'complete' : 'provisional'" data-testid="candle-state">
          {{ legend.is_complete ? 'COMPLETE' : 'PROVISIONAL' }}
        </span>
        <span class="muted">rev {{ legend.revision }} · {{ legend.origin === 'history' ? 'history bar' : legend.tick_count + ' ticks' }}</span>
      </template>
      <span v-if="legend.ind?.signal" class="tag" :class="legend.ind.provisional ? 'provisional' : 'signal-' + legend.ind.signal">
        {{ legend.ind.provisional ? 'provisional ' : '' }}{{ legend.ind.signal === 'up' ? 'up-cross' : 'down-cross' }}
      </span>
    </div>
    <div v-else class="legend muted">No candles yet for this symbol.</div>
  </div>
</template>

<style scoped>
.chart-wrap { position: relative; height: 100%; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
.chart-wrap.replay { border-color: #a371f7; box-shadow: 0 0 0 1px #a371f7 inset; }
.chart { position: absolute; inset: 0; }
.legend { position: absolute; top: 8px; left: 10px; right: 70px; z-index: 3; display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center;
  font-size: 12px; color: #c9d1d9; background: rgba(13,17,23,0.8); padding: 4px 8px; border-radius: 6px; pointer-events: none; width: fit-content; }
.lg-time { color: #8b949e; }
.up { color: #26a69a; } .down { color: #ef5350; }
.sma-fast b { color: #f0b429; } .sma-slow b { color: #58a6ff; }
.sma-fast { color: #f0b429; } .sma-slow { color: #58a6ff; }
.tag { font-size: 10px; font-weight: 700; letter-spacing: .04em; padding: 1px 6px; border-radius: 4px; }
.tag.complete { background: #1f6f43; color: #d2f8e0; }
.tag.provisional { background: #5a4a12; color: #ffe8a3; border: 1px dashed #d4a72c; }
.tag.signal-up { background: #1a7f37; color: #fff; }
.tag.signal-down { background: #b62324; color: #fff; }
.muted { color: #8b949e; }
</style>
