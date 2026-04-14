import { ref, onMounted, onUnmounted } from 'vue'
import { drainSyncQueue, cacheClear } from '../db/index.js'

const HEARTBEAT_INTERVAL_MS = 30_000
const HEARTBEAT_TIMEOUT_MS  = 5_000
const PING_URL = `${location.origin}/ping`

export function useOfflineSync(onReconnect) {
  const isOnline = ref(navigator.onLine)
  const isStandalone = ref(
    window.navigator.standalone === true ||
    window.matchMedia('(display-mode: standalone)').matches
  )

  let _heartbeatTimer = null

  async function handleOnline() {
    if (isOnline.value) return
    console.log('[Sync] Back online — triggering sync')
    isOnline.value = true
    await drainSyncQueue()
    if (onReconnect) {
      try { await onReconnect() } catch (e) { console.warn('[Sync] onReconnect failed:', e.message) }
    }
  }

  function handleOffline() {
    if (!isOnline.value) return
    console.log('[Sync] Gone offline')
    isOnline.value = false
  }

  async function runHeartbeat() {
    const controller = new AbortController()
    const timeoutId  = setTimeout(() => controller.abort(), HEARTBEAT_TIMEOUT_MS)
    try {
      const res = await fetch(PING_URL, { signal: controller.signal, cache: 'no-store' })
      res.ok ? await handleOnline() : handleOffline()
    } catch {
      // TypeError  → ERR_CONNECTION_REFUSED / no route to host
      // AbortError → 5 s timeout (ETIMEDOUT equivalent)
      handleOffline()
    } finally {
      clearTimeout(timeoutId)
    }
  }

  function startHeartbeat() {
    clearInterval(_heartbeatTimer)
    _heartbeatTimer = setInterval(runHeartbeat, HEARTBEAT_INTERVAL_MS)
  }

  async function onBrowserOnline() {
    // Verify server is actually reachable; OS event alone is insufficient
    await runHeartbeat()
  }

  function onBrowserOffline() {
    // Trust this direction immediately — no internet ⇒ no local API
    handleOffline()
  }

  async function onVisibilityChange() {
    if (document.visibilityState === 'visible') {
      // setInterval is throttled in background tabs; re-probe on foreground
      await runHeartbeat()
    }
  }

  onMounted(async () => {
    window.addEventListener('online',  onBrowserOnline)
    window.addEventListener('offline', onBrowserOffline)
    document.addEventListener('visibilitychange', onVisibilityChange)

    if (isStandalone.value) {
      cacheClear().catch((e) => console.warn('[IDB] cache prune failed:', e.message))
    }

    // Probe immediately on mount — do not trust initial navigator.onLine value
    await runHeartbeat()
    startHeartbeat()
  })

  onUnmounted(() => {
    window.removeEventListener('online',  onBrowserOnline)
    window.removeEventListener('offline', onBrowserOffline)
    document.removeEventListener('visibilitychange', onVisibilityChange)
    clearInterval(_heartbeatTimer)
    _heartbeatTimer = null
  })

  return { isOnline, isStandalone }
}
