// Reconnecting WebSocket to the FastAPI backend (/ws).
// The browser never talks to Yahoo directly: Yahoo -> backend -> this socket.

export function createSocket({ onMessage, onState }) {
  let ws = null
  let attempts = 0
  let closedByUs = false
  let retryTimer = null
  const queue = []

  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`

  function open() {
    onState({ state: 'connecting', attempts })
    ws = new WebSocket(url)
    ws.onopen = () => {
      attempts = 0
      onState({ state: 'open', attempts })
      while (queue.length) ws.send(queue.shift())
    }
    ws.onmessage = (ev) => {
      try {
        onMessage(JSON.parse(ev.data))
      } catch (e) {
        console.error('bad message from backend', e)
      }
    }
    ws.onclose = () => {
      if (closedByUs) return
      attempts += 1
      const delay = Math.min(10000, 500 * 2 ** Math.min(attempts, 5))
      onState({ state: 'closed', attempts, retryInMs: delay })
      retryTimer = setTimeout(open, delay)
    }
    ws.onerror = () => {} // onclose follows and handles retry
  }

  function send(obj) {
    const data = JSON.stringify(obj)
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(data)
    else queue.push(data)
  }

  function close() {
    closedByUs = true
    clearTimeout(retryTimer)
    ws?.close()
  }

  open()
  return { send, close }
}
