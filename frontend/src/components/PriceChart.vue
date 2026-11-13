<script setup>
// Candlesticks + volume with TradingView Lightweight Charts (v4).
// Provisional (incomplete) candles are drawn hollow/translucent so a viewer
// can tell a still-forming bar from a settled one.
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { createChart, CrosshairMode } from 'lightweight-charts'
import { fmtPrice, fmtVolume } from '../lib/format'

const props = defineProps({ mode: { type: String, default: 'live' } })

const el = ref(null)
const legend = ref(null)
const chart = shallowRef(null)
let candleSeries = null
let volumeSeries = null
const bars = new Map() // time -> wire candle
let resizeObs = null

const UP = '#26a69a'
const DOWN = '#ef5350'

function toCandle(c) {
  const up = c.close >= c.open
  const base = up ? UP : DOWN
  if (c.is_complete) return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }
  return {
    time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
    color: up ? 'rgba(38,166,154,0.15)' : 'rgba(239,83,80,0.15)',
    borderColor: base, wickColor: base,
  }
}

function toVolume(c) {
  const up = c.close >= c.open
  const alpha = c.is_complete ? 0.55 : 0.25
  return { time: c.time, value: c.volume, color: up ? `rgba(38,166,154,${alpha})` : `rgba(239,83,80,${alpha})` }
}

function redrawAll() {
  const sorted = [...bars.values()].sort((a, b) => a.time - b.time)
  candleSeries.setData(sorted.map(toCandle))
  volumeSeries.setData(sorted.map(toVolume))
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
    redrawAll() // revision of an older candle (late tick, finalisation): library can only update the last bar
  }
  showLegend(null)
}

function clear() {
  bars.clear()
  candleSeries.setData([])
  volumeSeries.setData([])
  showLegend(null)
}

function lastCandle() {
  if (!bars.size) return null
  return bars.get(Math.max(...bars.keys()))
}

function showLegend(time) {
  const c = time != null ? bars.get(time) : lastCandle()
  legend.value = c
}

defineExpose({ setCandles, upsert, clear, lastCandle })

onMounted(() => {
  chart.value = createChart(el.value, {
    autoSize: true,
    layout: { background: { color: '#0d1117' }, textColor: '#9aa4b2', fontFamily: 'Inter, system-ui, sans-serif' },
    grid: { vertLines: { color: '#161b22' }, horzLines: { color: '#161b22' } },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false, rightOffset: 6 },
    localization: {
      // chart times are UTC seconds; show them in the viewer's local time
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
  volumeSeries = chart.value.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' })
  chart.value.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
  chart.value.subscribeCrosshairMove((p) => showLegend(p?.time ?? null))
})

onBeforeUnmount(() => {
  resizeObs?.disconnect()
  chart.value?.remove()
})
</script>

<template>
  <div class="chart-wrap" :class="{ replay: props.mode === 'replay' }">
    <div ref="el" class="chart" data-testid="chart"></div>
    <div v-if="legend" class="legend" data-testid="legend">
      <span class="lg-time">{{ new Date(legend.bucket_start_ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }}</span>
      <span>O <b>{{ fmtPrice(legend.open) }}</b></span>
      <span>H <b>{{ fmtPrice(legend.high) }}</b></span>
      <span>L <b>{{ fmtPrice(legend.low) }}</b></span>
      <span>C <b :class="legend.close >= legend.open ? 'up' : 'down'">{{ fmtPrice(legend.close) }}</b></span>
      <span>Vol <b>{{ fmtVolume(legend.volume) }}</b></span>
      <span class="tag" :class="legend.is_complete ? 'complete' : 'provisional'" data-testid="candle-state">
        {{ legend.is_complete ? 'COMPLETE' : 'PROVISIONAL' }}
      </span>
      <span class="muted">rev {{ legend.revision }} · {{ legend.origin === 'history' ? 'history bar' : legend.tick_count + ' ticks' }}</span>
    </div>
    <div v-else class="legend muted">No candles yet for this symbol.</div>
  </div>
</template>

<style scoped>
.chart-wrap { position: relative; height: 100%; min-height: 380px; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }
.chart-wrap.replay { border-color: #a371f7; box-shadow: 0 0 0 1px #a371f7 inset; }
.chart { position: absolute; inset: 0; }
.legend { position: absolute; top: 8px; left: 10px; z-index: 3; display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  font-size: 12px; color: #c9d1d9; background: rgba(13,17,23,0.8); padding: 4px 8px; border-radius: 6px; pointer-events: none; }
.lg-time { color: #8b949e; }
.up { color: #26a69a; } .down { color: #ef5350; }
.tag { font-size: 10px; font-weight: 700; letter-spacing: .04em; padding: 1px 6px; border-radius: 4px; }
.tag.complete { background: #1f6f43; color: #d2f8e0; }
.tag.provisional { background: #5a4a12; color: #ffe8a3; border: 1px dashed #d4a72c; }
.muted { color: #8b949e; }
</style>
