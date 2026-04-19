import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'

const MASKED = 'Rp ••••••••'

export function useFmt() {
  const store = useFinanceStore()
  const fmt = (n) => store.hideNumbers ? MASKED : formatIDR(n ?? 0)
  return { fmt }
}
