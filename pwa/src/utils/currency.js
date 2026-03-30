const idrFormatter = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
})

export function formatIDR(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return 'Rp 0'
  }

  const n = Number(value)
  return `Rp ${idrFormatter.format(Math.abs(Math.round(n)))}`
}
