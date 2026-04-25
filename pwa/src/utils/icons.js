const S = `viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"`

export const GROUP_SVGS = {
  'Financial & Legal':    `<svg ${S}><line x1="10" y1="3" x2="10" y2="17"/><line x1="4" y1="8" x2="16" y2="8"/><path d="M4 8l-2 4h4z"/><path d="M16 8l-2 4h4z"/><line x1="6" y1="17" x2="14" y2="17"/></svg>`,
  'Health & Family':      `<svg ${S}><path d="M10 16.5S3 12 3 7.5A3.5 3.5 0 0110 5a3.5 3.5 0 017 2.5C17 12 10 16.5 10 16.5z"/></svg>`,
  'Housing & Bills':      `<svg ${S}><path d="M3 9.5L10 3l7 6.5"/><path d="M5.5 8.5V17h3v-3.5h3V17h3V8.5"/></svg>`,
  'Travel':               `<svg ${S}><path d="M2 13l4-4 2.5 2.5L14 5l4 3"/><line x1="2" y1="17" x2="18" y2="17"/></svg>`,
  'Lifestyle & Personal': `<svg ${S}><path d="M5.5 3h9l1.5 5H4L5.5 3z"/><path d="M4 8v8a1 1 0 001 1h10a1 1 0 001-1V8"/></svg>`,
  'Food & Dining':        `<svg ${S}><line x1="7" y1="3" x2="7" y2="17"/><path d="M4 3v5a3 3 0 006 0V3"/><line x1="13" y1="3" x2="13" y2="17"/></svg>`,
  'Transportation':       `<svg ${S}><rect x="2" y="9" width="16" height="7" rx="1.5"/><path d="M5 9V7a3 3 0 016 0v2"/><circle cx="5.5" cy="16" r="1"/><circle cx="14.5" cy="16" r="1"/></svg>`,
  'System / Tracking':    `<svg ${S}><circle cx="10" cy="10" r="3"/><path d="M10 3v2M10 15v2M3 10h2M15 10h2"/></svg>`,
}

export const NAV_SVGS = {
  Dashboard:       `<svg ${S}><rect x="3" y="3" width="6" height="6" rx="1"/><rect x="11" y="3" width="6" height="6" rx="1"/><rect x="3" y="11" width="6" height="6" rx="1"/><rect x="11" y="11" width="6" height="6" rx="1"/></svg>`,
  Flows:           `<svg ${S}><polyline points="2,14 7,9 11,12 18,5"/></svg>`,
  Wealth:          `<svg ${S}><circle cx="10" cy="10" r="7"/><path d="M10 7v3l2 2"/></svg>`,
  Assets:          `<svg ${S}><rect x="2" y="7" width="16" height="11" rx="1.5"/><path d="M6 7V5a4 4 0 018 0v2"/></svg>`,
  Transactions:    `<svg ${S}><line x1="4" y1="6" x2="16" y2="6"/><line x1="4" y1="10" x2="16" y2="10"/><line x1="4" y1="14" x2="10" y2="14"/></svg>`,
  Goal:            `<svg ${S}><circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="3"/><circle cx="10" cy="10" r="0.8" fill="currentColor" stroke="none"/></svg>`,
  Review:          `<svg ${S}><path d="M13 3H5a1 1 0 00-1 1v12a1 1 0 001 1h10a1 1 0 001-1V7l-3-4z"/><polyline points="13,3 13,7 16,7"/><polyline points="7,12 9,14 13,10"/></svg>`,
  'Foreign Spend': `<svg ${S}><circle cx="10" cy="10" r="7"/><line x1="10" y1="3" x2="10" y2="17"/><path d="M13.5 7c0 0-1-1.5-3.5-1.5S6.5 7 6.5 7s1 1.5 3.5 1.5S13.5 7 13.5 7"/><path d="M13.5 13c0 0-1 1.5-3.5 1.5S6.5 13 6.5 13s1-1.5 3.5-1.5 3.5 1.5 3.5 1.5"/></svg>`,
  Adjustment:      `<svg ${S}><line x1="4" y1="10" x2="16" y2="10"/><line x1="4" y1="6" x2="10" y2="6"/><line x1="10" y1="14" x2="16" y2="14"/><circle cx="12" cy="6" r="1.5"/><circle cx="8" cy="14" r="1.5"/></svg>`,
  Audit:           `<svg ${S}><path d="M13 3H5a1 1 0 00-1 1v12a1 1 0 001 1h10a1 1 0 001-1V7l-3-4z"/><polyline points="13,3 13,7 16,7"/><polyline points="7,12 9,14 13,10"/></svg>`,
  Settings:        `<svg ${S}><circle cx="10" cy="10" r="2.5"/><path d="M10 3v1.5M10 15.5v1.5M3 10h1.5M15.5 10H17M5.2 5.2l1 1M13.8 13.8l1 1M14.8 5.2l-1 1M6.2 13.8l-1 1"/></svg>`,
}

export const KPI_SVGS = {
  assets:    `<svg ${S}><rect x="2" y="7" width="16" height="11" rx="1.5"/><path d="M6 7V5a4 4 0 018 0v2"/><line x1="10" y1="11" x2="10" y2="14"/><line x1="8.5" y1="12.5" x2="11.5" y2="12.5"/></svg>`,
  liability: `<svg ${S}><circle cx="10" cy="10" r="7"/><line x1="7" y1="10" x2="13" y2="10"/></svg>`,
  income:    `<svg ${S}><polyline points="2,14 7,9 11,12 18,5"/><polyline points="14,5 18,5 18,9"/></svg>`,
  spending:  `<svg ${S}><polyline points="2,6 7,11 11,8 18,15"/><polyline points="14,15 18,15 18,11"/></svg>`,
}

export const SECTION_SVGS = {
  cash:        `<svg ${S}><rect x="2" y="7" width="16" height="11" rx="1.5"/><path d="M6 7V5a4 4 0 018 0v2"/><line x1="10" y1="11" x2="10" y2="14"/><line x1="8.5" y1="12.5" x2="11.5" y2="12.5"/></svg>`,
  investments: `<svg ${S}><polyline points="2,14 7,9 11,12 18,5"/><polyline points="14,5 18,5 18,9"/></svg>`,
  bonds:       `<svg ${S}><rect x="4" y="2" width="12" height="16" rx="1"/><line x1="7" y1="7" x2="13" y2="7"/><line x1="7" y1="10" x2="13" y2="10"/><line x1="7" y1="13" x2="10" y2="13"/></svg>`,
  stocks:      `<svg ${S}><polyline points="2,14 7,9 11,12 18,5"/></svg>`,
  funds:       `<svg ${S}><circle cx="10" cy="10" r="7"/><path d="M7 10h6M10 7v6"/></svg>`,
  property:    `<svg ${S}><path d="M3 9.5L10 3l7 6.5"/><path d="M5.5 8.5V17h3v-3.5h3V17h3V8.5"/></svg>`,
  retirement:  `<svg ${S}><path d="M10 3a7 7 0 110 14"/><path d="M10 3a7 7 0 010 14"/><path d="M5 10h10"/><path d="M10 6v8"/></svg>`,
  liability:   `<svg ${S}><circle cx="10" cy="10" r="7"/><line x1="10" y1="7" x2="10" y2="11"/><circle cx="10" cy="13.5" r="0.7" fill="currentColor" stroke="none"/></svg>`,
}

export const EYE_SVG      = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 10s3.5-6 8-6 8 6 8 6-3.5 6-8 6-8-6-8-6z"/><circle cx="10" cy="10" r="2.5"/></svg>`
export const CAMERA_SVG   = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 7h2l2-2h8l2 2h2a1 1 0 011 1v8a1 1 0 01-1 1H2a1 1 0 01-1-1V8a1 1 0 011-1z"/><circle cx="10" cy="11" r="3"/></svg>`
export const COIN_SVG     = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="7"/><line x1="10" y1="7" x2="10" y2="13"/><line x1="7.5" y1="8.5" x2="12.5" y2="8.5"/><line x1="7.5" y1="11.5" x2="12.5" y2="11.5"/></svg>`
export const SPARKLE_SVG  = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2l1.5 5.5L17 9l-5.5 1.5L10 16l-1.5-5.5L3 9l5.5-1.5z"/></svg>`
export const REFRESH_SVG  = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M16 10a6 6 0 10-1.76 4.24"/><polyline points="16,4 16,10 10,10"/></svg>`
export const SAVE_SVG     = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 3h10l3 3v11H4z"/><path d="M7 3v5h6V3"/><path d="M7 17v-5h6v5"/></svg>`
export const FOLDER_SVG   = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6h5l2 2h9v8.5A1.5 1.5 0 0116.5 18h-13A1.5 1.5 0 012 16.5z"/></svg>`
export const CHECK_SVG    = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><polyline points="4,10.5 8,14.5 16,6.5"/></svg>`
export const X_SVG        = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="5" x2="15" y2="15"/><line x1="15" y1="5" x2="5" y2="15"/></svg>`
export const PEN_SVG      = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14.5l-.8 2.3 2.3-.8L15 6.5 13.5 5z"/><path d="M12.5 4l1.5-1.5a1.4 1.4 0 012 2L14.5 6"/></svg>`
export const ROBOT_SVG    = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="6" width="12" height="9" rx="2"/><path d="M10 3v3"/><circle cx="8" cy="10.5" r="0.8" fill="currentColor" stroke="none"/><circle cx="12" cy="10.5" r="0.8" fill="currentColor" stroke="none"/><path d="M8 13h4"/></svg>`
export const POINTER_SVG  = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l8 8-3 1 1.5 4-1.8.7-1.5-4-3 1z"/></svg>`
export const DOCUMENT_SVG = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h6l4 4v10H6z"/><polyline points="12,3 12,7 16,7"/><line x1="8" y1="11" x2="14" y2="11"/><line x1="8" y1="14" x2="12" y2="14"/></svg>`
export const DATABASE_SVG = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="10" cy="5" rx="6" ry="2.5"/><path d="M4 5v6c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V5"/><path d="M4 8c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5"/></svg>`
export const INFO_SVG     = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="7"/><line x1="10" y1="9" x2="10" y2="13"/><circle cx="10" cy="6.2" r="0.8" fill="currentColor" stroke="none"/></svg>`
export const GLOBE_SVG    = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="7"/><path d="M3 10h14"/><path d="M10 3c2 2 3 4.5 3 7s-1 5-3 7c-2-2-3-4.5-3-7s1-5 3-7"/></svg>`

// ── Category-level SVGs (thin-stroke monochrome, 20×20) ────────────────────
// Maps each finance category name to its icon.  Used by FinancialStatementModal
// and any other view that renders per-category icons.  Fall back to group icon
// via GROUP_SVGS when a category is not listed here.
export const CATEGORY_SVGS = {
  // Financial & Legal
  'Fees & Interest':        `<svg ${S}><circle cx="10" cy="10" r="7"/><line x1="10" y1="7" x2="10" y2="13"/><line x1="7.5" y1="8.5" x2="12.5" y2="8.5"/><line x1="7.5" y1="11.5" x2="12.5" y2="11.5"/></svg>`,
  'Taxes':                  `<svg ${S}><path d="M6 3h6l4 4v10H6z"/><polyline points="12,3 12,7 16,7"/><line x1="8" y1="11" x2="14" y2="11"/><line x1="8" y1="14" x2="11" y2="14"/></svg>`,
  'Professional Expenses':  `<svg ${S}><rect x="2" y="6" width="16" height="11" rx="1.5"/><path d="M6 6V4.5A1.5 1.5 0 017.5 3h5A1.5 1.5 0 0114 4.5V6"/><line x1="10" y1="9" x2="10" y2="12"/></svg>`,
  // Food & Dining
  'Groceries':              `<svg ${S}><path d="M3 3h2l1.5 9h9L17 6H6"/><circle cx="8" cy="16" r="1.2"/><circle cx="14" cy="16" r="1.2"/></svg>`,
  'Dining Out':             `<svg ${S}><line x1="6" y1="3" x2="6" y2="9"/><path d="M3 3v4a3 3 0 006 0V3"/><line x1="14" y1="3" x2="14" y2="17"/><path d="M11 3v3a3 3 0 006 0V3"/><line x1="3" y1="17" x2="8" y2="17"/></svg>`,
  'Delivery & Takeout':     `<svg ${S}><rect x="3" y="6" width="14" height="10" rx="1.5"/><path d="M6 6V4a4 4 0 018 0v2"/><line x1="3" y1="11" x2="17" y2="11"/></svg>`,
  // Health & Family
  'Healthcare':             `<svg ${S}><line x1="10" y1="5" x2="10" y2="15"/><line x1="5" y1="10" x2="15" y2="10"/><rect x="3" y="3" width="14" height="14" rx="2"/></svg>`,
  'Family':                 `<svg ${S}><circle cx="10" cy="6" r="2.5"/><path d="M5 17v-2a3 3 0 016 0v2"/><circle cx="15" cy="7" r="1.5"/><path d="M15 10a3 3 0 012 2.8V15"/></svg>`,
  'Education':              `<svg ${S}><path d="M2 9l8-4 8 4-8 4z"/><path d="M5 11v4c0 1 2 2 5 2s5-1 5-2v-4"/></svg>`,
  'Gifts & Donations':      `<svg ${S}><rect x="3" y="8" width="14" height="9" rx="1"/><path d="M3 11h14"/><path d="M10 8v9"/><path d="M10 8c-2-3-5-3-5 0h5c2-3 5-3 5 0h-5"/></svg>`,
  // Housing & Bills
  'Housing':                `<svg ${S}><path d="M3 9.5L10 3l7 6.5"/><path d="M5.5 8.5V17h3v-3.5h3V17h3V8.5"/></svg>`,
  'Utilities':              `<svg ${S}><polyline points="11,2 6,11 10,11 9,18 14,9 10,9"/></svg>`,
  'Phone Bill':             `<svg ${S}><rect x="5" y="2" width="10" height="16" rx="2"/><circle cx="10" cy="15" r="1"/></svg>`,
  'Internet':               `<svg ${S}><circle cx="10" cy="10" r="7"/><path d="M3 10h14"/><path d="M10 3c2 2 3 4.5 3 7s-1 5-3 7c-2-2-3-4.5-3-7s1-5 3-7"/></svg>`,
  'Household':              `<svg ${S}><path d="M4 17V8l6-5 6 5v9H4z"/><path d="M8 17v-4h4v4"/></svg>`,
  // Lifestyle & Personal
  'Shopping':               `<svg ${S}><path d="M5.5 3h9l1.5 5H4L5.5 3z"/><path d="M4 8v8a1 1 0 001 1h10a1 1 0 001-1V8"/></svg>`,
  'Personal Care':          `<svg ${S}><path d="M7 2l-2 16"/><path d="M13 2l2 16"/><path d="M5 6h10"/><path d="M5 10h10"/></svg>`,
  'Entertainment':          `<svg ${S}><circle cx="10" cy="10" r="7"/><polygon points="8,7 14,10 8,13"/></svg>`,
  'Subscriptions':          `<svg ${S}><path d="M16 10a6 6 0 10-1.76 4.24"/><polyline points="16,4 16,10 10,10"/></svg>`,
  'Hobbies':                `<svg ${S}><path d="M10 2l1.5 5.5L17 9l-5.5 1.5L10 16l-1.5-5.5L3 9l5.5-1.5z"/></svg>`,
  // System / Tracking (income & meta)
  'Earned Income':          `<svg ${S}><polyline points="2,14 7,9 11,12 18,5"/><polyline points="14,5 18,5 18,9"/></svg>`,
  'Investment Income':      `<svg ${S}><polyline points="2,14 7,9 11,12 18,5"/><polyline points="14,5 18,5 18,9"/><line x1="4" y1="17" x2="16" y2="17"/></svg>`,
  'Interest Income':        `<svg ${S}><path d="M3 9.5L10 3l7 6.5"/><path d="M5.5 8.5V17h3v-3.5h3V17h3V8.5"/><line x1="8" y1="6" x2="12" y2="6"/></svg>`,
  'Capital Gains':          `<svg ${S}><rect x="3" y="10" width="3" height="7"/><rect x="8.5" y="6" width="3" height="11"/><rect x="14" y="3" width="3" height="14"/></svg>`,
  'Passive Income':         `<svg ${S}><circle cx="8" cy="10" r="5"/><circle cx="13" cy="10" r="5"/><line x1="10.5" y1="7" x2="10.5" y2="13"/></svg>`,
  'Other Income':           `<svg ${S}><line x1="10" y1="5" x2="10" y2="15"/><line x1="7" y1="8" x2="13" y2="8"/><line x1="7" y1="12" x2="13" y2="12"/><circle cx="10" cy="10" r="7"/></svg>`,
  'Transfer':               `<svg ${S}><polyline points="4,7 7,4 7,9"/><line x1="7" y1="6.5" x2="13" y2="6.5"/><polyline points="16,13 13,16 13,11"/><line x1="7" y1="13.5" x2="13" y2="13.5"/></svg>`,
  'Cash Withdrawal':        `<svg ${S}><rect x="2" y="5" width="16" height="10" rx="1.5"/><line x1="6" y1="8" x2="6" y2="12"/><circle cx="10" cy="10" r="2"/></svg>`,
  'Adjustment':             `<svg ${S}><line x1="4" y1="10" x2="16" y2="10"/><line x1="4" y1="6" x2="10" y2="6"/><line x1="10" y1="14" x2="16" y2="14"/><circle cx="12" cy="6" r="1.5"/><circle cx="8" cy="14" r="1.5"/></svg>`,
  'Other':                  `<svg ${S}><circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="0.8" fill="currentColor" stroke="none"/><circle cx="10" cy="6" r="0.8" fill="currentColor" stroke="none"/><circle cx="10" cy="14" r="0.8" fill="currentColor" stroke="none"/></svg>`,
  // Transportation
  'Auto':                   `<svg ${S}><rect x="2" y="9" width="16" height="7" rx="1.5"/><path d="M5 9V7a3 3 0 016 0v2"/><circle cx="5.5" cy="16" r="1"/><circle cx="14.5" cy="16" r="1"/></svg>`,
  'Rideshare':              `<svg ${S}><rect x="2" y="9" width="16" height="7" rx="1.5"/><path d="M5 9V7a3 3 0 016 0v2"/><circle cx="5.5" cy="16" r="1"/><circle cx="14.5" cy="16" r="1"/><line x1="7" y1="12" x2="13" y2="12"/></svg>`,
  // Travel
  'Flights & Hotels':       `<svg ${S}><path d="M4 14l5-5 2 2 5-5"/><line x1="2" y1="17" x2="18" y2="17"/><path d="M16 5l2-1v4l-2-1"/></svg>`,
  'Vacation Spending':      `<svg ${S}><path d="M3 14c2-3 4-5 7-5s5 2 7 5"/><line x1="2" y1="17" x2="18" y2="17"/><path d="M10 9V4"/><path d="M7 6l3-2 3 2"/></svg>`,
}

/** Resolve a category SVG by name, falling back to its group icon. */
export function categorySvg(name, categoryGroup) {
  return CATEGORY_SVGS[name] || GROUP_SVGS[categoryGroup] || GROUP_SVGS['System / Tracking']
}
