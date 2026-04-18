import { clientsClaim } from 'workbox-core'
import { precacheAndRoute, cleanupOutdatedCaches } from 'workbox-precaching'
import { registerRoute } from 'workbox-routing'
import { StaleWhileRevalidate, NetworkFirst } from 'workbox-strategies'
import { ExpirationPlugin } from 'workbox-expiration'
import { CacheableResponsePlugin } from 'workbox-cacheable-response'

console.log('[SW] Installing service worker...')

self.addEventListener('install', (event) => {
  console.log('[SW] install event fired')
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  console.log('[SW] activate event fired — now controlling all clients')
  event.waitUntil(enforceStorageQuota())
})

self.addEventListener('fetch', (event) => {
  if (event.request.url.includes('/api/')) {
    console.log('[SW] fetch intercepted:', event.request.method, event.request.url)
  }
})

clientsClaim()
self.__WB_DISABLE_DEV_LOGS = false

cleanupOutdatedCaches()
precacheAndRoute(self.__WB_MANIFEST)

registerRoute(
  ({ request }) =>
    request.destination === 'style' ||
    request.destination === 'script' ||
    request.destination === 'image' ||
    request.destination === 'font',
  new StaleWhileRevalidate({
    cacheName: 'static-assets',
    plugins: [
      new ExpirationPlugin({ maxEntries: 100, maxAgeSeconds: 7 * 24 * 60 * 60 }),
      new CacheableResponsePlugin({ statuses: [0, 200] }),
    ],
  })
)

// Wealth endpoints mutate frequently — always fetch from network first so
// a POST (upsert/snapshot) is immediately visible in the next GET.
registerRoute(
  ({ url }) => url.pathname.startsWith('/api/wealth/'),
  new NetworkFirst({
    cacheName: 'api-cache-wealth',
    networkTimeoutSeconds: 8,
    plugins: [
      new ExpirationPlugin({ maxEntries: 30, maxAgeSeconds: 10 * 60 }),
      new CacheableResponsePlugin({ statuses: [0, 200] }),
    ],
  })
)

registerRoute(
  ({ url }) =>
    url.pathname.startsWith('/api/') &&
    !url.pathname.startsWith('/api/wealth/') &&
    !url.pathname.endsWith('/sync') &&
    !url.pathname.endsWith('/import') &&
    !url.pathname.endsWith('/alias') &&
    !url.pathname.startsWith('/api/ai/') &&
    !url.pathname.startsWith('/api/audit/') &&
    !url.pathname.startsWith('/api/pdf/local-workspace'),
  new StaleWhileRevalidate({
    cacheName: 'api-cache',
    plugins: [
      new ExpirationPlugin({ maxEntries: 60, maxAgeSeconds: 10 * 60 }),
      new CacheableResponsePlugin({ statuses: [0, 200] }),
    ],
  })
)

registerRoute(
  ({ url }) =>
    url.pathname.endsWith('/sync') ||
    url.pathname.endsWith('/import') ||
    url.pathname.endsWith('/alias') ||
    url.pathname.startsWith('/api/ai/'),
  new NetworkFirst({
    cacheName: 'mutations-cache',
    networkTimeoutSeconds: 10,
    plugins: [
      new CacheableResponsePlugin({ statuses: [0, 200] }),
      new ExpirationPlugin({ maxEntries: 50, maxAgeSeconds: 300 }),
    ],
  })
)

async function enforceStorageQuota() {
  const estimate = self.navigator?.storage?.estimate
  if (!estimate) return

  const { usage } = await estimate.call(self.navigator.storage)
  const LIMIT = 45 * 1024 * 1024
  if (usage > LIMIT) {
    console.log('[SW] Storage quota exceeded — pruning api-cache')
    const cache = await caches.open('api-cache')
    const keys = await cache.keys()
    const toPrune = Math.ceil(keys.length * 0.2)
    for (let i = 0; i < toPrune; i += 1) {
      await cache.delete(keys[i])
    }
    console.log(`[SW] Pruned ${toPrune} cache entries`)
  }
}
