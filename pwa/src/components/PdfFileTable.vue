<template>
  <div class="pdf-file-list">
    <!-- Header row -->
    <div class="pdf-row pdf-row--header">
      <div>PDF File</div>
      <div>Last Processed</div>
      <div>Status</div>
    </div>

    <template v-for="(file, i) in files" :key="file.key">
      <!-- Bank group separator -->
      <div
        v-if="i === 0 || file.institutionKey !== files[i - 1].institutionKey"
        :class="['pdf-bank-separator', { 'pdf-bank-separator--ruled': i > 0 }]"
      >
        <span class="pdf-bank-label">{{ file.institutionLabel }}</span>
      </div>

      <!-- Data row — label makes the whole row clickable for the checkbox -->
      <label class="pdf-row pdf-row--data">
        <div class="pdf-col-file">
          <input
            v-model="file.selected"
            type="checkbox"
            :disabled="processing"
          />
          <div class="pdf-name-cell">
            <div class="pdf-name-main">{{ file.filename }}</div>
            <div class="pdf-name-sub">
              <span v-if="file.relativeDir">{{ file.relativeDir }}</span>
              <span v-else>{{ file.institutionLabel }}</span>
            </div>
          </div>
        </div>
        <div class="pdf-col-date" :title="formatPdfDate(file.lastProcessedAt, true)">
          {{ formatPdfDate(file.lastProcessedAt, false) }}
        </div>
        <div class="pdf-col-status">
          <span :class="['pdf-status-chip', `pdf-status-${getPdfStatusClass(file)}`]">
            {{ getPdfStatusLabel(file) }}
          </span>
        </div>
      </label>
    </template>
  </div>
</template>

<script setup>
import { formatPdfDate, getPdfStatusClass, getPdfStatusLabel } from '../utils/pdfFormatters.js'

defineProps({
  files: { type: Array, required: true },
  processing: { type: Boolean, default: false },
})
</script>

<style scoped>
.pdf-file-list {
  font-size: 13px;
}

.pdf-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 130px 90px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}

.pdf-file-list > *:last-child {
  border-bottom: none;
}

/* Header */
.pdf-row--header {
  background: rgba(255,255,255,0.03);
  border-bottom: 1px solid rgba(255,255,255,0.10) !important;
  cursor: default;
  user-select: none;
}

.pdf-row--header > * {
  padding: 10px 12px;
  font-size: 11.5px;
  font-weight: 700;
  color: rgba(191,219,254,0.88);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

/* Data rows */
.pdf-row--data {
  cursor: pointer;
  color: rgba(255,255,255,0.82);
}

.pdf-row--data:hover {
  background: rgba(59,130,246,0.10);
}

.pdf-row--data > * {
  padding: 11px 12px;
  display: flex;
  align-items: center;
}

/* Column 1: checkbox + filename */
.pdf-col-file {
  gap: 10px;
  align-items: flex-start !important;
  min-width: 0;
}

.pdf-col-file input[type="checkbox"] {
  flex: 0 0 auto;
  margin-top: 3px;
  accent-color: #60a5fa;
}

.pdf-name-cell {
  flex: 1;
  min-width: 0;
}

.pdf-name-main {
  font-weight: 600;
  color: rgba(255,255,255,0.95);
  word-break: break-word;
}

.pdf-name-sub {
  margin-top: 3px;
  font-size: 11.5px;
  color: rgba(191,219,254,0.72);
}

/* Column 2 & 3 */
.pdf-col-date {
  color: rgba(255,255,255,0.72);
  font-size: 12.5px;
}

/* Bank group separator */
.pdf-bank-separator {
  padding: 14px 12px 5px;
  background: rgba(96,165,250,0.04);
  border-bottom: none !important;
}

.pdf-bank-separator--ruled {
  border-top: 1px solid rgba(255,255,255,0.10);
}

.pdf-bank-label {
  font-size: 11.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: rgba(147,197,253,0.90);
}

/* Status chips */
.pdf-status-chip {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.pdf-status-new        { background: rgba(148,163,184,0.18); color: rgba(226,232,240,0.95); }
.pdf-status-pending    { background: rgba(59,130,246,0.18);  color: #93c5fd; }
.pdf-status-processing { background: rgba(96,165,250,0.24);  color: #dbeafe; }
.pdf-status-ok         { background: rgba(34,197,94,0.18);   color: #86efac; }
.pdf-status-partial    { background: rgba(245,158,11,0.18);  color: #fcd34d; }
.pdf-status-skipped    { background: rgba(245,158,11,0.18);  color: #fcd34d; }
.pdf-status-error      { background: rgba(239,68,68,0.18);   color: #fca5a5; }
</style>
