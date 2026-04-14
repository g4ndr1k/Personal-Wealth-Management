import { openDB } from 'idb'

const DB_NAME = 'finance-app'
const DB_VERSION = 1

export const db = openDB(DB_NAME, DB_VERSION, {
  upgrade(db) {
    if (!db.objectStoreNames.contains('cache')) {
      const store = db.createObjectStore('cache', { keyPath: 'key' })
      store.createIndex('updatedAt', 'updatedAt')
    }

    if (!db.objectStoreNames.contains('syncQueue')) {
      const q = db.createObjectStore('syncQueue', { keyPath: 'id', autoIncrement: true })
      q.createIndex('createdAt', 'createdAt')
    }
  },
})

export async function cacheSet(key, value) {
  const conn = await db
  await conn.put('cache', { key, value, updatedAt: Date.now() })
}

export async function cacheGetEntry(key) {
  const conn = await db
  return (await conn.get('cache', key)) || null
}

export async function cacheGet(key) {
  const entry = await cacheGetEntry(key)
  return entry?.value ?? null
}

export async function cacheDelete(key) {
  const conn = await db
  await conn.delete('cache', key)
}

export async function cacheClearAll() {
  const conn = await db
  await conn.clear('cache')
}

export async function cacheClear(maxAgeMs = 24 * 60 * 60 * 1000) {
  const conn = await db
  const cutoff = Date.now() - maxAgeMs
  const tx = conn.transaction('cache', 'readwrite')
  let cursor = await tx.store.index('updatedAt').openCursor()
  let pruned = 0

  while (cursor && cursor.value.updatedAt < cutoff) {
    await cursor.delete()
    pruned += 1
    cursor = await cursor.continue()
  }

  await tx.done
  console.log(`[IDB] Pruned ${pruned} stale cache entries`)
}

export async function queueMutation(method, url, body, headers = {}) {
  const conn = await db
  await conn.add('syncQueue', {
    method,
    url,
    body,
    headers,
    createdAt: Date.now(),
  })
  console.log('[IDB] Queued mutation:', method, url)
}

export async function drainSyncQueue() {
  const conn = await db
  const all = (await conn.getAll('syncQueue')).sort((a, b) => a.createdAt - b.createdAt)
  if (!all.length) return

  console.log(`[IDB] Draining ${all.length} queued mutations...`)

  for (const item of all) {
    try {
      const hasBody = item.body !== undefined && item.body !== null
      const headers = {
        ...(item.headers || {}),
        ...(hasBody ? { 'Content-Type': 'application/json' } : {}),
      }
      const res = await fetch(item.url, {
        method: item.method,
        headers,
        body: hasBody ? JSON.stringify(item.body) : undefined,
      })
      if (res.ok) {
        await conn.delete('syncQueue', item.id)
        console.log('[IDB] Synced and removed queued item:', item.id)
      } else {
        console.warn('[IDB] Replay failed for item', item.id, res.status, res.statusText)
      }
    } catch (e) {
      console.warn('[IDB] Sync retry failed for item', item.id, e.message)
    }
  }
}
