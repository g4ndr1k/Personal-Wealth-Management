/**
 * Format IDR amount with dot as thousands separator.
 * e.g. 125000 → "Rp 125.000"
 */
export function formatIDR(amount) {
  if (amount == null) return ''
  return 'Rp ' + String(amount).replace(/\B(?=(\d{3})+(?!\d))/g, '.')
}

/**
 * Get current date as YYYY-MM-DD in local timezone.
 */
export function todayLocal() {
  const d = new Date()
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/**
 * Get current time as HH:MM in local timezone.
 */
export function nowLocal() {
  const d = new Date()
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * Build ISO 8601 local datetime string from date + time inputs.
 * e.g. "2026-04-20T14:30:00+07:00"
 */
export function buildTxnDatetime(date, time) {
  return `${date}T${time}:00+07:00`
}

/**
 * Display datetime as "20 Apr 2026, 14:30"
 */
export function displayDatetime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun',
                  'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des']
  return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}, ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function pad(n) {
  return String(n).padStart(2, '0')
}
