<template>
  <div :class="['settings-page', { 'settings-page--desktop': isDesktop }]">

    <div v-if="!isDesktop" class="section-hd">
      <span class="settings-head-icon" v-html="NAV_SVGS.Settings"></span> Settings
    </div>

    <nav v-if="isDesktop" class="settings-sub-nav">
      <div class="settings-sub-nav__title">Settings</div>
      <template v-for="group in NAV_GROUPS" :key="group.label">
        <div class="settings-sub-nav__group-label">{{ group.label }}</div>
        <button
          v-for="item in group.items"
          :key="item.id"
          class="settings-sub-nav__item"
          :class="{ 'is-active': activeSection === item.id }"
          @click="setActiveSection(item.id)"
        >
          <span class="settings-sub-nav__icon" v-html="item.icon"></span>
          <span>{{ item.label }}</span>
        </button>
        <div class="settings-sub-nav__divider"></div>
      </template>
    </nav>

    <div class="settings-content">
      <div v-if="isDesktop" class="section-hd">
        <span class="settings-head-icon" v-html="NAV_SVGS.Settings"></span> Settings
      </div>

      <Transition name="settings-fade" mode="out-in">
      <div :key="isDesktop ? activeSection : 'all'" class="settings-grid">

    <ReadOnlyBanner />

    <div class="settings-section-label" v-if="!isDesktop">More Views</div>

    <div v-if="!isDesktop" class="setting-card">
      <div class="setting-title"><span class="setting-title-icon" v-html="NAV_SVGS.Settings"></span>Quick Navigation</div>
      <div class="setting-desc">
        Open views that live under More on mobile PWA.
      </div>
      <div class="more-nav-grid">
        <RouterLink to="/foreign" class="more-nav-card">
          <span class="more-nav-card__icon" v-html="NAV_SVGS['Foreign Spend']"></span>
          <span class="more-nav-card__body">
            <span class="more-nav-card__title">Foreign Spend</span>
            <span class="more-nav-card__desc">Foreign currency transactions and FX totals.</span>
          </span>
          <span class="more-nav-card__chevron">›</span>
        </RouterLink>
        <RouterLink to="/audit" class="more-nav-card">
          <span class="more-nav-card__icon" v-html="NAV_SVGS.Audit"></span>
          <span class="more-nav-card__body">
            <span class="more-nav-card__title">Audit</span>
            <span class="more-nav-card__desc">Call-over comparison and PDF completeness audit.</span>
          </span>
          <span class="more-nav-card__chevron">›</span>
        </RouterLink>
      </div>
    </div>

    <div class="settings-section-label" v-show="!isDesktop">Preferences & Reference Data</div>

    <div class="setting-card" v-show="!isDesktop || activeSection === 'dashboard-range'">
      <div class="setting-title"><span class="setting-title-icon" v-html="NAV_SVGS.Dashboard"></span> Dashboard Range</div>
      <div class="setting-desc">
        Choose which months appear on the main dashboard. Months before Jan 2026 are always hidden.
      </div>
      <div class="setting-row setting-row-range">
        <div class="range-field">
          <label class="range-label">Start Month</label>
          <select
            class="range-select"
            :value="store.dashboardStartMonth"
            @change="store.setDashboardRange($event.target.value, store.dashboardEndMonth)"
          >
            <option
              v-for="option in store.dashboardMonthOptions.filter(option => option.value <= store.dashboardEndMonth)"
              :key="`start-${option.value}`"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </div>
        <div class="range-field">
          <label class="range-label">End Month</label>
          <select
            class="range-select"
            :value="store.dashboardEndMonth"
            @change="store.setDashboardRange(store.dashboardStartMonth, $event.target.value)"
          >
            <option
              v-for="option in store.dashboardMonthOptions.filter(option => option.value >= store.dashboardStartMonth)"
              :key="`end-${option.value}`"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </div>
      </div>
      <div class="setting-desc" style="margin-top:10px">
        Active range: <strong>{{ store.dashboardRangeLabel }}</strong>
      </div>
    </div>

    <div class="setting-card" v-show="!isDesktop || activeSection === 'categories'">
      <div class="setting-title"><span class="setting-title-icon" v-html="DOCUMENT_SVG"></span> Categories</div>
      <div class="setting-desc">
        Add a new category or edit an existing one. Renaming a category also updates existing transactions, overrides, and aliases.
      </div>

      <div class="setting-row">
        <label class="range-label" for="category-preset-select">Category</label>
        <div class="category-editor-toolbar">
          <select
            id="category-preset-select"
            data-testid="category-preset-select"
            class="range-select"
            :value="categorySelection"
            @change="onCategorySelectionChange($event.target.value)"
          >
            <option value="__new__">＋ New category</option>
            <option v-for="category in editableCategories" :key="category.category" :value="category.category">
              {{ category.category }}
            </option>
          </select>
          <button class="btn btn-ghost btn-sm" type="button" @click="resetCategoryEditor">New</button>
        </div>
      </div>

      <div class="category-editor-grid">
        <div class="setting-row">
          <label class="range-label">Name</label>
          <input data-testid="category-name-input" v-model="categoryForm.category" class="form-input" type="text" placeholder="e.g. Dining Out" />
        </div>
        <div class="setting-row">
          <label class="range-label">Icon</label>
          <input data-testid="category-icon-input" v-model="categoryForm.icon" class="form-input" type="text" placeholder="Optional text/icon" />
        </div>
        <div class="setting-row">
          <label class="range-label">Sort Order</label>
          <input data-testid="category-sort-order-input" v-model="categoryForm.sort_order" class="form-input" type="number" min="0" step="1" />
        </div>
        <div class="setting-row">
          <label class="range-label">Monthly Budget</label>
          <input data-testid="category-budget-input" v-model="categoryForm.monthly_budget" class="form-input" type="number" min="0" step="1000" placeholder="Optional" />
        </div>
        <div class="setting-row">
          <label class="range-label">Group</label>
          <input data-testid="category-group-input" v-model="categoryForm.category_group" class="form-input" type="text" placeholder="e.g. Living" />
        </div>
        <div class="setting-row">
          <label class="range-label">Subcategory</label>
          <input data-testid="category-subcategory-input" v-model="categoryForm.subcategory" class="form-input" type="text" placeholder="e.g. Meals" />
        </div>
      </div>

      <label class="check-label" style="margin-top:10px">
        <input data-testid="category-recurring-input" v-model="categoryForm.is_recurring" type="checkbox" />
        Recurring category
      </label>

      <div class="category-editor-actions">
        <button
          data-testid="category-save-button"
          class="btn btn-primary"
          :disabled="categoryEditorState.loading || !categoryForm.category.trim()"
          @click="saveCategoryEditor"
        >
          <span v-if="categoryEditorState.loading"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Saving…</span>
          <span v-else><span class="inline-icon" v-html="SAVE_SVG"></span> Save Category</span>
        </button>
      </div>

      <div v-if="categoryEditorState.error" class="alert alert-error" style="margin-top:10px">
        {{ categoryEditorState.error }}
      </div>
      <div v-else-if="categoryEditorState.success" class="alert alert-success" style="margin-top:10px">
        {{ categoryEditorState.success }}
      </div>
    </div>

    <!-- Health status -->
    <div class="setting-card" v-show="!isDesktop || activeSection === 'api-status'">
      <div class="setting-title"><span class="setting-title-icon" v-html="INFO_SVG"></span> API Status</div>
      <div v-if="!store.health" class="loading" style="padding:10px 0"><div class="spinner"></div> Checking…</div>
      <div v-else>
        <div class="status-grid">
          <div class="status-item">
            <div class="sk">Transactions</div>
            <div class="sv api-status-sv">
              <span class="api-status-dot" :class="store.health.status === 'ok' ? 'api-status-dot--ok' : 'api-status-dot--err'"></span>
              {{ store.health.transaction_count?.toLocaleString() ?? '—' }}
            </div>
          </div>
          <div class="status-item">
            <div class="sk">Needs Review</div>
            <div class="sv" :class="store.health.needs_review > 0 ? 'text-expense' : 'text-income'">
              {{ store.health.needs_review ?? '—' }}
            </div>
          </div>
          <div class="status-item" style="grid-column:1/-1">
            <div class="sk">Last Sync</div>
            <div class="sv" style="font-size:13px">{{ store.health.last_sync || 'Never' }}</div>
          </div>
        </div>
        <button class="btn btn-ghost btn-sm" style="margin-top:12px" @click="store.loadHealth({ forceFresh: true })">
          <span class="inline-icon" v-html="REFRESH_SVG"></span> Refresh status
        </button>
      </div>
    </div>

    <div class="setting-card" v-show="!isDesktop || activeSection === 'mobile-cache'">
      <div class="setting-title"><span class="setting-title-icon" v-html="DATABASE_SVG"></span> Mobile Data Cache</div>
      <div class="setting-desc">
        iPhone PWA reads cached API data for up to 24 hours. Use this to pull fresh data immediately instead of waiting for the daily refresh window.
      </div>
      <button
        class="btn btn-primary btn-block"
        :disabled="refreshCacheState.loading"
        @click="refreshMobileCache"
      >
        <span v-if="refreshCacheState.loading"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Refreshing cache…</span>
        <span v-else><span class="inline-icon" v-html="REFRESH_SVG"></span> Refresh Mobile Data Now</span>
      </button>
      <div v-if="refreshCacheState.error" class="alert alert-error" style="margin-top:10px">
        {{ refreshCacheState.error }}
      </div>
      <div v-else-if="refreshCacheState.doneAt" class="result-box">
        <div class="result-row">
          <span class="rk">Last manual refresh</span>
          <span class="rv">{{ refreshCacheState.doneAt }}</span>
        </div>
      </div>
    </div>

    <div class="settings-section-label" v-show="!isDesktop" v-if="!store.isReadOnly">Desktop Operations</div>

    <!-- Import from XLSX -->
    <div v-if="!store.isReadOnly" class="setting-card" v-show="!isDesktop || activeSection === 'import-xlsx'">
      <div class="setting-title"><span class="setting-title-icon" v-html="DOCUMENT_SVG"></span> Import from XLSX</div>
      <div class="setting-desc">
        Read
        <code style="font-size:11px;background:var(--bg);padding:2px 5px;border-radius:3px">ALL_TRANSACTIONS.xlsx</code>
        and import new rows directly into SQLite. Duplicate rows (matched by hash) are skipped unless "Overwrite" is on.
        A backup is created automatically after a successful import.
      </div>

      <div class="setting-row">
        <label>
          <input type="checkbox" v-model="importOpts.dry_run" />
          Dry run (preview only, no writes)
        </label>
      </div>
      <div class="setting-row">
        <label>
          <input type="checkbox" v-model="importOpts.overwrite" />
          Overwrite existing rows (re-import duplicates)
        </label>
      </div>

      <button
        class="btn btn-primary btn-block"
        style="margin-top:4px"
        :disabled="importState.loading"
        @click="doImport"
      >
        <span v-if="importState.loading"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Importing…</span>
        <span v-else><span class="inline-icon" v-html="DOCUMENT_SVG"></span> {{ importOpts.dry_run ? 'Dry Run' : 'Import' }}</span>
      </button>

      <!-- Import result -->
      <div v-if="importState.error" class="alert alert-error" style="margin-top:10px">
        {{ importState.error }}
      </div>
      <div v-else-if="importState.result" class="result-box">
        <div class="result-row">
          <span class="rk">Rows added</span>
          <span class="rv" :class="(importState.result.rows_added || 0) > 0 ? 'text-income' : 'text-neutral'">
            {{ importState.result.rows_added ?? 0 }}
          </span>
        </div>
        <div v-if="importOpts.dry_run" class="result-row">
          <span class="rk">Mode</span>
          <span class="rv" style="color:var(--warning)">Dry run — no changes written</span>
        </div>
      </div>
    </div>

    <div v-if="!store.isReadOnly" class="setting-card" v-show="!isDesktop || activeSection === 'pdf-pipeline'">
      <div class="setting-title"><span class="setting-title-icon" v-html="DOCUMENT_SVG"></span> PDF Pipeline</div>
      <div class="setting-desc">
        Run the end-to-end pipeline from <code style="font-size:11px;background:var(--bg);padding:2px 5px;border-radius:3px">data/pdf_inbox</code>
        through import and sync. Desktop only, and controlled by the bridge pipeline setting.
      </div>

      <div class="pipeline-grid">
        <button
          class="btn btn-primary"
          :disabled="pipelineState.loading || pipelineState.status?.status === 'running'"
          @click="runPipeline"
        >
          <span v-if="pipelineState.loading || pipelineState.status?.status === 'running'">
            <span class="spinner" style="width:14px;height:14px;border-width:2px"></span>
            Running pipeline…
          </span>
          <span v-else><span class="inline-icon" v-html="REFRESH_SVG"></span> Run Pipeline</span>
        </button>

        <div class="result-box" v-if="pipelineState.status">
          <div class="result-row">
            <span class="rk">Status</span>
            <span class="rv">{{ pipelineState.status.status || 'idle' }}</span>
          </div>
          <div class="result-row">
            <span class="rk">Last run</span>
            <span class="rv">{{ pipelineState.status.last_run_at || 'Never' }}</span>
          </div>
          <div class="result-row">
            <span class="rk">Next run</span>
            <span class="rv">{{ pipelineState.status.next_scheduled_at || 'Not scheduled' }}</span>
          </div>
          <div class="result-row">
            <span class="rk">Last result</span>
            <span class="rv">
              {{ formatPipelineSummary(pipelineState.status.last_result) }}
            </span>
          </div>
        </div>
      </div>

      <div v-if="pipelineState.error" class="alert alert-error" style="margin-top:10px">
        {{ pipelineState.error }}
      </div>
    </div>

    <!-- ── Process Local PDFs ──────────────────────────────────────────────── -->
    <div v-if="!store.isReadOnly" class="setting-card" v-show="!isDesktop || activeSection === 'process-pdfs'">
      <div class="setting-title"><span class="setting-title-icon" v-html="FOLDER_SVG"></span> Process Local PDFs</div>
      <div class="setting-desc">
        Scan <code style="font-size:11px;background:var(--bg);padding:2px 5px;border-radius:3px">data/pdf_inbox</code>
        and <code style="font-size:11px;background:var(--bg);padding:2px 5px;border-radius:3px">data/pdf_unlocked</code>
        for bank statement PDFs. Review the list, see when each file was last processed,
        and run only the PDFs you select.
      </div>

      <!-- Non-Mac notice -->
      <div v-if="!isDesktopMac" class="pdf-unavail-note">
        Only available on macOS desktop. Open this app on your Mac controller.
      </div>

      <div class="pdf-desktop-tools">
        <div class="pdf-desktop-actions">
          <span
            class="pdf-btn-wrapper"
            :title="!isDesktopMac ? 'This feature is only available on the Desktop controller.' : ''"
          >
            <button
              class="btn btn-ghost btn-sm"
              :disabled="!isDesktopMac"
              :class="{ 'btn-disabled-look': !isDesktopMac }"
              @click="togglePdfWorkspace"
            >
              {{ showPdfWorkspace ? 'Hide PDF Workspace' : 'Open PDF Workspace' }}
            </button>
          </span>
        </div>

        <div v-if="showPdfWorkspace" class="pdf-workspace">
          <div class="pdf-layout">

            <!-- ── LEFT: Controls sidebar ── -->
            <div class="pdf-controls">
              <div class="pdf-controls-toolbar">
                <button
                  class="btn btn-ghost btn-sm"
                  :disabled="pdfWorkspace.loading || pdf.phase === 'processing'"
                  @click="loadPdfWorkspace"
                >
                  {{ pdfWorkspace.loading ? 'Refreshing…' : 'Refresh' }}
                </button>
                <input
                  v-model.trim="pdfWorkspace.search"
                  class="pdf-search"
                  type="search"
                  placeholder="Search filename…"
                  :disabled="pdfWorkspace.loading"
                />
                <select
                  v-model="pdfWorkspace.folder"
                  class="pdf-filter"
                  :disabled="pdfWorkspace.loading"
                >
                  <option value="all">All folders</option>
                  <option value="pdf_inbox">pdf_inbox</option>
                  <option value="pdf_unlocked">pdf_unlocked</option>
                </select>
              </div>

              <div v-if="pdf.fatalError" class="alert alert-error">
                {{ pdf.fatalError }}
              </div>
              <div v-else-if="pdfWorkspace.error" class="alert alert-error">
                {{ pdfWorkspace.error }}
              </div>

              <div v-if="pdf.phase === 'processing' && pdf.total > 0" class="pdf-progress-panel">
                <div class="pdf-summary-bar">
                  <span class="pdf-badge pdf-badge-ok"><span class="inline-icon" v-html="CHECK_SVG"></span>{{ pdfCounts.ok }}</span>
                  <span v-if="pdfCounts.skipped > 0" class="pdf-badge pdf-badge-skip">{{ pdfCounts.skipped }} skipped</span>
                  <span v-if="pdfCounts.error > 0" class="pdf-badge pdf-badge-err"><span class="inline-icon" v-html="X_SVG"></span>{{ pdfCounts.error }} failed</span>
                  <span class="pdf-badge pdf-badge-pend">{{ pdf.processed }} / {{ pdf.total }}</span>
                </div>
                <div class="pdf-progress-bar-wrap" style="margin-top:8px">
                  <div
                    class="pdf-progress-bar"
                    :style="{ width: Math.round(100 * pdf.processed / pdf.total) + '%' }"
                  ></div>
                </div>
                <div v-if="pdf.current" class="pdf-current-file">↳ {{ pdf.current }}</div>
              </div>

              <label v-if="!pdfWorkspace.loading && visiblePdfFiles.length > 0" class="pdf-master-toggle">
                <input
                  type="checkbox"
                  :checked="allVisibleSelected"
                  :disabled="pdf.phase === 'processing'"
                  @change="toggleVisibleSelection($event.target.checked)"
                />
                <span>Select all matching PDFs</span>
              </label>

              <div class="pdf-controls-footer">
                <div class="pdf-selection-note">
                  {{ selectedPdfCount }} selected · {{ readyToProcessFiles.length }} ready · {{ visiblePdfFiles.length }} shown · {{ pdfWorkspace.files.length }} total
                </div>
                <div class="pdf-controls-actions">
                  <!-- Pre-flight errors -->
                  <div v-if="preflightState.errors.length" class="preflight-errors" style="grid-column:1/-1">
                    <div v-for="(err, i) in preflightState.errors" :key="i" class="preflight-error-item">
                      ⚠ {{ err }}
                    </div>
                  </div>
                  <div v-if="preflightState.warnings.length" class="preflight-warnings" style="grid-column:1/-1">
                    <div v-for="(w, i) in preflightState.warnings" :key="i" class="preflight-warning-item">
                      ⚡ {{ w }}
                    </div>
                  </div>
                  <!-- Post-run warning: 0 processed -->
                  <div v-if="pdfProcessWarning" class="preflight-warnings" style="grid-column:1/-1">
                    <div class="preflight-warning-item">⚠ {{ pdfProcessWarning }}</div>
                  </div>
                  <button
                    class="btn btn-ghost btn-sm"
                    :disabled="selectedPdfCount === 0 || pdf.phase === 'processing'"
                    @click="clearPdfSelection"
                  >
                    Clear Selection
                  </button>
                  <button
                    class="btn btn-primary btn-sm"
                    :disabled="pdfProcessableCount === 0 || pdf.phase === 'processing' || pdfWorkspace.loading"
                    @click="processSelectedPdfs"
                  >
                    <span v-if="pdf.phase === 'processing'">
                      <span class="spinner" style="width:14px;height:14px;border-width:2px"></span>
                      Processing {{ pdf.processed }} / {{ pdf.total }}…
                    </span>
                    <span v-else>
                      {{ selectedPdfCount > 0 ? 'Process Selected' : 'Process Ready PDFs' }}
                    </span>
                  </button>
                </div>
              </div>
            </div>

            <!-- ── RIGHT: Content area ── -->
            <div class="pdf-content">
              <div v-if="pdfWorkspace.loading" class="pdf-empty-state">
                <span class="spinner" style="width:16px;height:16px;border-width:2px"></span>
                Loading local PDFs…
              </div>

              <template v-else>
                <div v-if="visiblePdfFiles.length === 0" class="pdf-empty-state">
                  {{ pdfWorkspace.files.length === 0 ? 'No PDF files found in pdf_inbox or pdf_unlocked.' : 'No PDFs match the current search or folder filter.' }}
                </div>

                <div v-else class="pdf-groups">
                  <!-- Ready to Process (aggregate view) -->
                  <section class="pdf-group-card">
                    <button
                      class="pdf-group-header"
                      type="button"
                      @click="pdfExpanded.readyToProcess = !pdfExpanded.readyToProcess"
                    >
                      <div class="pdf-group-title">
                        <span class="pdf-group-chevron">{{ pdfExpanded.readyToProcess ? '▾' : '▸' }}</span>
                        <span>Ready to Process</span>
                      </div>
                      <div class="pdf-group-meta">
                        <span>{{ readyToProcessFiles.length }} PDFs</span>
                        <span>{{ groupedReadyToProcess.length }} banks</span>
                      </div>
                    </button>
                    <div v-if="pdfExpanded.readyToProcess" class="pdf-month-list">
                      <template v-if="readyToProcessFiles.length === 0">
                        <div class="pdf-empty-state" style="padding:16px">All processed</div>
                      </template>
                      <template v-else>
                        <PdfFileTable :files="readyToProcessFiles" :processing="pdf.phase === 'processing'" />
                      </template>
                    </div>
                  </section>

                  <!-- Bank sections -->
                  <section
                    v-for="institution in groupedPdfFiles"
                    :key="institution.key"
                    class="pdf-group-card"
                  >
                    <button
                      class="pdf-group-header"
                      type="button"
                      @click="toggleInstitutionGroup(institution.key)"
                    >
                      <div class="pdf-group-title">
                        <span class="pdf-group-chevron">{{ isInstitutionExpanded(institution.key) ? '▾' : '▸' }}</span>
                        <span>{{ institution.label }}</span>
                      </div>
                      <div class="pdf-group-meta">
                        <span>{{ institution.fileCount }} PDFs</span>
                        <span>{{ institution.months.length }} months</span>
                      </div>
                    </button>

                    <div v-if="isInstitutionExpanded(institution.key)" class="pdf-month-list">
                      <section
                        v-for="month in institution.months"
                        :key="month.key"
                        class="pdf-month-card"
                      >
                        <button
                          class="pdf-month-header"
                          type="button"
                          @click="toggleMonthGroup(month.key)"
                        >
                          <div class="pdf-group-title">
                            <span class="pdf-group-chevron">{{ isMonthExpanded(month.key) ? '▾' : '▸' }}</span>
                            <span>{{ month.label }}</span>
                          </div>
                          <div class="pdf-group-meta">
                            <span>{{ month.files.length }} PDFs</span>
                          </div>
                        </button>

                        <div v-if="isMonthExpanded(month.key)" class="pdf-table-wrap">
                          <PdfFileTable :files="month.files" :processing="pdf.phase === 'processing'" />
                        </div>
                      </section>
                    </div>
                  </section>
                </div>
              </template>
            </div>

          </div>
        </div>
      </div>
    </div>

    <div v-if="!store.isReadOnly" class="setting-card setting-card--collapsible" v-show="!isDesktop || activeSection === 'backup'">
      <button
        class="setting-card__toggle"
        type="button"
        :aria-expanded="String(!backupCollapsed)"
        @click="backupCollapsed = !backupCollapsed"
      >
        <div class="setting-card__toggle-title">
          <span class="setting-title-icon" v-html="SAVE_SVG"></span>
          <span>Backup</span>
        </div>
        <span class="setting-card__toggle-chevron" :class="{ 'is-open': !backupCollapsed }">⌄</span>
      </button>
      <div v-if="backupCollapsed" class="setting-card__collapsed-note">
        Collapsed by default. Open to create backups or review retention tiers.
      </div>
      <div v-else class="setting-card__body">
        <div class="setting-desc">
          Tiered SQLite backups live in <code style="font-size:11px;background:var(--bg);padding:2px 5px;border-radius:3px">{{ backupState.status?.backup_root || '~/agentic-ai/data/backups' }}</code>.
          Auto backups keep 24 hourly, 31 daily, 5 weekly, and 12 monthly sets. Manual backups keep 10 sets.
        </div>
        <button
          data-testid="manual-backup-button"
          class="btn btn-primary btn-block"
          :disabled="backupState.loading"
          @click="doManualBackup"
        >
          <span v-if="backupState.loading"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Creating backup…</span>
          <span v-else><span class="inline-icon" v-html="SAVE_SVG"></span> Create Manual Backup</span>
        </button>
        <div v-if="backupState.error" class="alert alert-error" style="margin-top:10px">
          {{ backupState.error }}
        </div>
        <div v-else-if="backupState.result" class="alert alert-success" style="margin-top:10px">
          Saved {{ baseName(backupState.result.path) }}
        </div>
        <div v-if="backupTiers.length" class="backup-tier-list">
          <div v-for="tier in backupTiers" :key="tier.key" class="backup-tier-card">
            <div class="backup-tier-row">
              <div>
                <div class="backup-tier-title">{{ tier.label }}</div>
                <div class="backup-tier-sub">{{ tier.count }} / {{ tier.max_sets }} kept</div>
              </div>
              <span :class="['backup-tier-pill', `backup-tier-pill--${tier.status}`]">{{ backupStatusLabel(tier.status) }}</span>
            </div>
            <div class="backup-tier-meta">Latest: {{ fmtRelativeTime(tier.latest_at) }}</div>
            <div v-if="tier.next_due_at" class="backup-tier-meta">Next due: {{ fmtRelativeTime(tier.next_due_at) }}</div>
            <div v-if="tier.latest_file" class="backup-tier-file">{{ baseName(tier.latest_file) }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- NAS Sync (only when writable and NAS is configured) -->
    <div v-if="!store.isReadOnly && nasSyncStatus.configured" class="setting-card" v-show="!isDesktop || activeSection === 'nas-sync'">
      <div class="setting-title"><span class="setting-title-icon" v-html="REFRESH_SVG"></span> NAS Sync</div>
      <div class="setting-desc">
        Push the latest available backup to the NAS replica so the always-on copy stays current.
      </div>
      <div v-if="nasSyncStatus.last_synced_at" style="font-size:12px;color:var(--text-muted);margin-bottom:10px">
        Last synced: {{ fmtNasSync(nasSyncStatus.last_synced_at) }}
      </div>
      <button
        class="btn btn-primary btn-block"
        :disabled="nasSyncState.loading"
        @click="doNasSync"
      >
        <span v-if="nasSyncState.loading"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Syncing…</span>
        <span v-else><span class="inline-icon" v-html="REFRESH_SVG"></span> Sync to NAS Now</span>
      </button>
      <div v-if="nasSyncState.result" style="margin-top:10px;font-size:12px">
        <div v-if="nasSyncState.result.ok" class="alert alert-success" style="padding:6px 8px">
          ✓ Synced at {{ fmtNasSync(nasSyncState.result.synced_at) }}
        </div>
        <div v-else class="alert alert-error" style="padding:6px 8px">
          {{ nasSyncState.result.error || 'Sync failed' }}
        </div>
      </div>
    </div>

    <div class="settings-section-label" v-show="!isDesktop">Connected Satellites & Automation</div>

    <div v-if="!store.isReadOnly" class="setting-card setting-card--collapsible" v-show="!isDesktop || activeSection === 'household'">
      <button
        class="setting-card__toggle"
        type="button"
        :aria-expanded="String(!householdCollapsed)"
        @click="householdCollapsed = !householdCollapsed"
      >
        <div class="setting-card__toggle-title">
          <span class="setting-title-icon" v-html="NAV_SVGS.Assets"></span>
          <span>Household Expense</span>
        </div>
        <span class="setting-card__toggle-chevron" :class="{ 'is-open': !householdCollapsed }">⌄</span>
      </button>
      <div v-if="householdCollapsed" class="setting-card__collapsed-note">
        Collapsed by default. Open to manage household categories, recent expenses, and cash pools.
      </div>
      <div v-else class="setting-card__body">
        <div class="setting-desc">
          Satellite household expense operations for the DS920+ LAN app on port 8088. Update recent transaction categories and adjust live cash pools without opening the assistant-facing PWA.
        </div>
        <div v-if="householdState.loading" class="loading" style="padding:10px 0"><div class="spinner"></div> Loading household workspace…</div>
        <div v-else-if="householdState.error" class="alert alert-error">
          {{ householdState.error }}
        </div>
        <div v-else-if="!householdState.available" class="alert alert-error">
          {{ householdState.unavailableReason || 'Household service unavailable' }}
        </div>
        <div v-else class="household-grid">
          <div class="household-pane household-pane--wide">
            <div class="household-pane__title">Categories</div>
            <div class="household-category-create">
              <div class="range-field">
                <label class="range-label">Code</label>
                <input
                  data-testid="household-category-code-input"
                  v-model="householdCategoryForm.code"
                  class="form-input"
                  type="text"
                  placeholder="e.g. fruit"
                />
              </div>
              <div class="range-field">
                <label class="range-label">Label</label>
                <input
                  data-testid="household-category-label-input"
                  v-model="householdCategoryForm.label_id"
                  class="form-input"
                  type="text"
                  placeholder="e.g. Buah"
                />
              </div>
              <div class="range-field">
                <label class="range-label">Sort</label>
                <input
                  data-testid="household-category-sort-input"
                  v-model="householdCategoryForm.sort_order"
                  class="form-input"
                  type="number"
                  min="0"
                  step="1"
                />
              </div>
              <div class="household-item__controls household-item__controls--compact">
                <button
                  data-testid="household-category-create-button"
                  class="btn btn-primary btn-sm"
                  :disabled="!householdCategoryForm.code.trim() || !householdCategoryForm.label_id.trim()"
                  @click="createHouseholdCategory"
                >
                  Add category
                </button>
              </div>
            </div>
            <div v-if="!householdState.categories.length" class="household-empty">No household categories yet.</div>
            <div v-for="category in householdState.categories" :key="category.originalCode" class="household-item">
              <div class="household-category-grid">
                <div class="range-field">
                  <label class="range-label">Code</label>
                  <input
                    :data-testid="`household-category-code-${category.originalCode}`"
                    v-model="category.draftCode"
                    class="form-input"
                    type="text"
                  />
                </div>
                <div class="range-field">
                  <label class="range-label">Label</label>
                  <input
                    :data-testid="`household-category-label-${category.originalCode}`"
                    v-model="category.draftLabel"
                    class="form-input"
                    type="text"
                  />
                </div>
                <div class="range-field">
                  <label class="range-label">Sort</label>
                  <input
                    :data-testid="`household-category-sort-${category.originalCode}`"
                    v-model="category.draftSortOrder"
                    class="form-input"
                    type="number"
                    min="0"
                    step="1"
                  />
                </div>
              </div>
              <div class="household-item__controls">
                <span class="household-item__meta">Live in Household PWA immediately after save.</span>
                <div class="household-action-row">
                  <button
                    :data-testid="`household-category-save-${category.originalCode}`"
                    class="btn btn-primary btn-sm"
                    :disabled="category.saving || !category.draftCode.trim() || !category.draftLabel.trim()"
                    @click="saveHouseholdCategory(category)"
                  >
                    {{ category.saving ? 'Saving…' : 'Save' }}
                  </button>
                  <button
                    :data-testid="`household-category-delete-${category.originalCode}`"
                    class="btn btn-ghost btn-sm household-delete-btn"
                    :disabled="category.deleting"
                    @click="removeHouseholdCategory(category)"
                  >
                    {{ category.deleting ? 'Removing…' : 'Remove' }}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div class="household-pane">
            <div class="household-pane__title">Recent expenses</div>
            <div v-if="!householdState.recentTransactions.length" class="household-empty">No recent household expenses found.</div>
            <div v-for="txn in householdState.recentTransactions" :key="txn.id" class="household-item">
              <div class="household-item__head">
                <div>
                  <div class="household-item__title">{{ householdTransactionLabel(txn) }}</div>
                  <div class="household-item__meta">{{ formatHouseholdDate(txn.txn_datetime) }} · {{ formatHouseholdAmount(txn.amount) }}</div>
                </div>
                <span class="household-chip">#{{ txn.id }}</span>
              </div>
              <div class="household-item__controls">
                <select
                  :data-testid="`household-transaction-category-${txn.id}`"
                  v-model="txn.draftCategory"
                  class="range-select"
                >
                  <option v-for="category in householdState.categories" :key="category.code" :value="category.code">
                    {{ category.label_id }}
                  </option>
                </select>
                <button
                  :data-testid="`household-transaction-save-${txn.id}`"
                  class="btn btn-primary btn-sm"
                  :disabled="txn.saving || !txn.draftCategory || txn.draftCategory === txn.category_code"
                  @click="saveHouseholdTransactionCategory(txn)"
                >
                  {{ txn.saving ? 'Saving…' : 'Save' }}
                </button>
              </div>
            </div>
          </div>

          <div class="household-pane">
            <div class="household-pane__title">Cash pools</div>
            <div v-if="!householdState.cashPools.length" class="household-empty">No household cash pools yet.</div>
            <div v-for="pool in householdState.cashPools" :key="pool.id" class="household-item household-item--pool">
              <div class="household-item__head">
                <div>
                  <div class="household-item__title">{{ pool.name }}</div>
                  <div class="household-item__meta">Code: {{ pool.code || '—' }}</div>
                </div>
                <span class="household-chip">#{{ pool.id }}</span>
              </div>
              <div class="household-item__controls household-item__controls--stacked">
                <div class="range-field">
                  <label class="range-label">Amount</label>
                  <input
                    :data-testid="`household-cashpool-amount-${pool.id}`"
                    v-model="pool.adjustmentInput"
                    class="form-input"
                    type="number"
                    step="1000"
                    placeholder="e.g. 50000"
                  />
                </div>
                <div class="range-field">
                  <label class="range-label">Notes</label>
                  <input
                    :data-testid="`household-cashpool-notes-${pool.id}`"
                    v-model="pool.notesInput"
                    class="form-input"
                    type="text"
                    placeholder="Optional notes"
                  />
                </div>
                <button
                  :data-testid="`household-cashpool-save-${pool.id}`"
                  class="btn btn-primary btn-sm"
                  :disabled="pool.saving || !pool.adjustmentInput"
                  @click="saveHouseholdCashPool(pool)"
                >
                  {{ pool.saving ? 'Saving…' : 'Apply adjustment' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>


    <div class="setting-card" v-show="!isDesktop || activeSection === 'mail-rules'">
      <div class="setting-title"><span class="setting-title-icon" v-html="MAIL_SVG"></span> Mail Rules</div>
      <div class="setting-desc">
        Emails matching sender addresses, domains, or subject keywords trigger iMessage alerts.
        Edit here to take effect on next scan.
      </div>

      <div class="mail-rules-section">
        <div class="mail-rules-label">Sender Emails</div>
        <div v-if="!emailRules.length" class="mail-rules-empty">No email rules yet.</div>
        <div v-for="rule in emailRules" :key="rule.id" class="mail-rule-row">
          <span class="mail-rule-pattern" :class="{ 'mail-rule-pattern--disabled': !rule.enabled }">{{ rule.pattern }}</span>
          <div class="mail-rule-actions">
            <button class="btn btn-ghost btn-sm" @click="toggleMailRule(rule)" :disabled="mailRulesState.loading">
              {{ rule.enabled ? 'Disable' : 'Enable' }}
            </button>
            <button class="btn btn-ghost btn-sm mail-rule-delete" @click="deleteMailRule(rule)" :disabled="mailRulesState.loading">
              <span class="inline-icon" v-html="X_SVG"></span>
            </button>
          </div>
        </div>
        <div class="mail-rule-add-row">
          <input
            v-model="mailRuleInputEmail"
            class="form-input"
            type="text"
            placeholder="e.g. alerts@mybank.com"
            @keydown.enter="addMailRule('sender_email')"
          />
          <button
            class="btn btn-primary btn-sm"
            :disabled="mailRulesState.loading || !mailRuleInputEmail.trim()"
            @click="addMailRule('sender_email')"
          >Add</button>
        </div>
      </div>

      <div class="mail-rules-section">
        <div class="mail-rules-label">Sender Domains</div>
        <div v-if="!domainRules.length" class="mail-rules-empty">No domain rules yet.</div>
        <div v-for="rule in domainRules" :key="rule.id" class="mail-rule-row">
          <span class="mail-rule-pattern" :class="{ 'mail-rule-pattern--disabled': !rule.enabled }">{{ rule.pattern }}</span>
          <div class="mail-rule-actions">
            <button class="btn btn-ghost btn-sm" @click="toggleMailRule(rule)" :disabled="mailRulesState.loading">
              {{ rule.enabled ? 'Disable' : 'Enable' }}
            </button>
            <button class="btn btn-ghost btn-sm mail-rule-delete" @click="deleteMailRule(rule)" :disabled="mailRulesState.loading">
              <span class="inline-icon" v-html="X_SVG"></span>
            </button>
          </div>
        </div>
        <div class="mail-rule-add-row">
          <input
            v-model="mailRuleInputDomain"
            class="form-input"
            type="text"
            placeholder="e.g. maybank.co.id"
            @keydown.enter="addMailRule('sender_domain')"
          />
          <button
            class="btn btn-primary btn-sm"
            :disabled="mailRulesState.loading || !mailRuleInputDomain.trim()"
            @click="addMailRule('sender_domain')"
          >Add</button>
        </div>
      </div>

      <div class="mail-rules-section">
        <div class="mail-rules-label">Subject Keywords</div>
        <div v-if="!keywordRules.length" class="mail-rules-empty">No keyword rules yet.</div>
        <div v-for="rule in keywordRules" :key="rule.id" class="mail-rule-row">
          <span class="mail-rule-pattern" :class="{ 'mail-rule-pattern--disabled': !rule.enabled }">{{ rule.pattern }}</span>
          <div class="mail-rule-actions">
            <button class="btn btn-ghost btn-sm" @click="toggleMailRule(rule)" :disabled="mailRulesState.loading">
              {{ rule.enabled ? 'Disable' : 'Enable' }}
            </button>
            <button class="btn btn-ghost btn-sm mail-rule-delete" @click="deleteMailRule(rule)" :disabled="mailRulesState.loading">
              <span class="inline-icon" v-html="X_SVG"></span>
            </button>
          </div>
        </div>
        <div class="mail-rule-add-row">
          <input
            v-model="mailRuleInputKeyword"
            class="form-input"
            type="text"
            placeholder="e.g. transfer"
            @keydown.enter="addMailRule('subject_keyword')"
          />
          <button
            class="btn btn-primary btn-sm"
            :disabled="mailRulesState.loading || !mailRuleInputKeyword.trim()"
            @click="addMailRule('subject_keyword')"
          >Add</button>
        </div>
      </div>

      <div v-if="mailRulesState.error" class="alert alert-error" style="margin-top:10px">{{ mailRulesState.error }}</div>
      <div v-else-if="mailRulesState.success" class="alert alert-success" style="margin-top:10px">{{ mailRulesState.success }}</div>
    </div>

    <div class="setting-card" v-show="!isDesktop || activeSection === 'ai-refinement'">
      <div class="setting-title"><span class="setting-title-icon" v-html="ROBOT_SVG"></span> AI Refinement</div>
      <div style="font-size:13px;color:var(--text-muted);margin-bottom:12px">
        When enabled, Flows and Wealth automatically call the local AI (Ollama) to enrich trend explanations, and Review Queue auto-generates category suggestions.
      </div>
      <label style="display:flex;align-items:center;gap:10px;cursor:pointer">
        <input
          type="checkbox"
          :checked="store.autoAiRefine"
          @change="store.setAutoAiRefine($event.target.checked)"
          style="width:16px;height:16px;cursor:pointer"
        />
        <span style="font-size:13px;font-weight:500">Auto-refine with AI on page load</span>
      </label>
      <div style="font-size:11px;color:var(--text-muted);margin-top:8px">
        When off, a <strong>Refine with AI</strong> button appears on each view so you can trigger it manually.
      </div>
    </div>

    <div class="setting-card" v-show="!isDesktop || activeSection === 'about'">
      <div class="setting-title"><span class="setting-title-icon" v-html="INFO_SVG"></span> About</div>
      <div class="about-stack">
        <div><strong>Finance Dashboard</strong> — Stage 3 wealth cockpit + operations console</div>
        <div>Vue 3 desktop/mobile PWA · FastAPI backend · SQLite authoritative store · local Ollama refinement</div>
        <div>Desktop Settings now also manages the Household Expense satellite on the DS920+ LAN app (port 8088).</div>
        <div v-if="store.isReadOnly" class="about-pill about-pill--readonly">
          <span class="inline-icon" v-html="EYE_SVG"></span> Read-only replica (NAS)
        </div>
        <div class="about-meta-row">
          <span class="about-meta-label">Finance API</span>
          <code style="font-size:11px">localhost:8090</code>
        </div>
        <div class="about-meta-row">
          <span class="about-meta-label">Household satellite</span>
          <code style="font-size:11px">{{ householdState.baseUrl || '192.168.1.44:8088' }}</code>
        </div>
      </div>
    </div>
    </div>
    </Transition>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import ReadOnlyBanner from '../components/ReadOnlyBanner.vue'
import PdfFileTable from '../components/PdfFileTable.vue'
import { formatPdfDate, formatPdfSize, truncateText, getPdfStatusClass, getPdfStatusLabel, getPdfDetail, isPdfReadyToProcess, groupFilesByInstitution } from '../utils/pdfFormatters.js'
import { useLayout } from '../composables/useLayout.js'
import { NAV_SVGS, EYE_SVG, REFRESH_SVG, SAVE_SVG, CHECK_SVG, X_SVG, ROBOT_SVG, DOCUMENT_SVG, FOLDER_SVG, DATABASE_SVG, INFO_SVG } from '../utils/icons.js'

const MAIL_SVG = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="16" height="11" rx="1.5"/><polyline points="2,5 10,12 18,5"/></svg>`

const store = useFinanceStore()
const { isDesktop } = useLayout()

// ── Desktop two-column nav state ──
const SETTINGS_SECTION_KEY = 'settings_active_section'

function readStoredSection() {
  try { return localStorage.getItem(SETTINGS_SECTION_KEY) || 'dashboard-range' } catch { return 'dashboard-range' }
}

const activeSection = ref(readStoredSection())

function setActiveSection(id) {
  activeSection.value = id
  try { localStorage.setItem(SETTINGS_SECTION_KEY, id) } catch {}
  if (isDesktop.value) {
    requestAnimationFrame(() => {
      document.querySelector('.settings-content')?.scrollTo({ top: 0 })
    })
  }
}

const showNasNav = computed(() => !store.isReadOnly && nasSyncStatus.value.configured)

const NAV_GROUPS = computed(() => [
  {
    label: 'Preferences',
    items: [
      { id: 'dashboard-range', label: 'Dashboard Range',  icon: NAV_SVGS.Dashboard },
      { id: 'categories',      label: 'Categories',       icon: DOCUMENT_SVG },
      { id: 'api-status',      label: 'API Status',       icon: INFO_SVG },
      { id: 'mobile-cache',    label: 'Mobile Data Cache', icon: DATABASE_SVG },
    ],
  },
  ...(!store.isReadOnly ? [{
    label: 'Desktop Ops',
    items: [
      { id: 'import-xlsx',  label: 'Import from XLSX',   icon: DOCUMENT_SVG },
      { id: 'pdf-pipeline', label: 'PDF Pipeline',       icon: REFRESH_SVG },
      { id: 'process-pdfs', label: 'Process Local PDFs', icon: FOLDER_SVG },
      { id: 'backup',       label: 'Backup',             icon: SAVE_SVG },
      ...(nasSyncStatus.value.configured ? [{ id: 'nas-sync', label: 'NAS Sync', icon: REFRESH_SVG }] : []),
    ],
  }] : []),
  {
    label: 'Connected',
    items: [
      ...(!store.isReadOnly ? [{ id: 'household', label: 'Household Expense', icon: NAV_SVGS.Assets }] : []),
      ...(!store.isReadOnly ? [{ id: 'mail-rules', label: 'Mail Rules',       icon: MAIL_SVG }] : []),
      { id: 'ai-refinement', label: 'AI Refinement', icon: ROBOT_SVG },
      { id: 'about',         label: 'About',         icon: INFO_SVG },
    ],
  },
])

const importState    = ref({ loading: false, result: null, error: null })
const backupState    = ref({ loading: false, error: null, status: null, result: null })
const backupCollapsed = ref(true)
const nasSyncState   = ref({ loading: false, result: null })
const nasSyncStatus  = ref({ configured: false, last_synced_at: null, target: null })
const importOpts  = ref({ dry_run: false, overwrite: false })
const refreshCacheState = ref({ loading: false, error: null, doneAt: '' })
const pipelineState = ref({ loading: false, status: null, error: null })
const categoryEditorState = ref({ loading: false, error: null, success: '' })
const mailRules          = ref([])
const mailRulesState     = ref({ loading: false, error: null, success: '' })
const mailRuleInputDomain  = ref('')
const mailRuleInputKeyword = ref('')
const mailRuleInputEmail   = ref('')
const showPdfWorkspace = ref(false)
const householdCollapsed = ref(true)
const householdState = ref({
  loading: false,
  error: null,
  unavailableReason: '',
  available: false,
  baseUrl: '',
  categories: [],
  recentTransactions: [],
  cashPools: [],
  success: '',
})
const householdCategoryForm = ref({ code: '', label_id: '', sort_order: 99 })

function makeEmptyCategoryForm() {
  return {
    original_category: '',
    category: '',
    icon: '',
    sort_order: 99,
    is_recurring: false,
    monthly_budget: '',
    category_group: '',
    subcategory: '',
  }
}

const categorySelection = ref('__new__')
const categoryForm = ref(makeEmptyCategoryForm())
const householdAmountFormatter = new Intl.NumberFormat('id-ID')

function formatHouseholdAmount(amount) {
  return `Rp ${householdAmountFormatter.format(Number(amount || 0))}`
}

function formatHouseholdDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('en-GB', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function householdTransactionLabel(txn) {
  return txn.description || txn.merchant || txn.category_code || `Expense #${txn.id}`
}

function normalizeHouseholdState(payload) {
  householdState.value = {
    loading: false,
    error: null,
    unavailableReason: payload.error || '',
    available: payload.available === true,
    baseUrl: payload.base_url || '',
    categories: (payload.categories || []).map(category => ({
      ...category,
      originalCode: category.code,
      draftCode: category.code,
      draftLabel: category.label_id,
      draftSortOrder: category.sort_order,
      saving: false,
      deleting: false,
    })),
    recentTransactions: (payload.recent_transactions || []).map(txn => ({
      ...txn,
      draftCategory: txn.category_code,
      saving: false,
    })),
    cashPools: (payload.cash_pools || []).map(pool => ({
      ...pool,
      adjustmentInput: '',
      notesInput: pool.notes || '',
      saving: false,
    })),
    success: '',
  }
}

// ── Mac desktop detection ────────────────────────────────────────────────────
// navigator.platform is "MacIntel" on macOS (and iPadOS ≥13 — exclude via maxTouchPoints).
const isDesktopMac = computed(() => {
  const platform     = navigator.platform || ''
  const ua           = navigator.userAgent || ''
  const looksLikeMac = platform.startsWith('Mac') || ua.includes('Macintosh')
  const isTouch      = navigator.maxTouchPoints > 1
  return looksLikeMac && !isTouch
})

// ── PDF processing state ─────────────────────────────────────────────────────
const EMPTY_PDF_STATE = () => ({
  phase: 'idle',   // 'idle' | 'processing'
  current: '',
  processed: 0,
  total: 0,
  fatalError: null,
  summary: null,   // { ok: number, error: number, partial: number } after run
})
const pdf = ref(EMPTY_PDF_STATE())
const pdfWorkspace = ref({
  loading: false,
  error: null,
  loaded: false,
  search: '',
  folder: 'all',
  files: [],
})
const pdfExpanded = ref({
  institutions: {},
  months: {},
  readyToProcess: true,
})
const preflightState = ref({ loading: false, error: null, errors: [], warnings: [] })
const pdfProcessWarning = ref('')

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// ── Derived Data Flow ─────────────────────────────────────────────────────
//
//   pdfWorkspace.files        (single source — raw from API)
//     → visiblePdfFiles        (search + folder filter)
//     → readyToProcessFiles    (filter: isPdfReadyToProcess, sorted oldest-first)
//     → groupedReadyToProcess  (groupFilesByInstitution)
//     → groupedPdfFiles        (groupFilesByInstitution, byPeriod: true)
//
//   All transformations are derived (no mutation of source).
//   pdfWorkspace.files is never modified by display logic.
// ──────────────────────────────────────────────────────────────────────────

const visiblePdfFiles = computed(() => {
  const q = pdfWorkspace.value.search.trim().toLowerCase()
  return pdfWorkspace.value.files
    .filter((file) => {
      const matchesFolder = pdfWorkspace.value.folder === 'all' || file.folder === pdfWorkspace.value.folder
      const haystack = `${file.filename} ${file.relativePath} ${file.institutionLabel}`.toLowerCase()
      const matchesSearch = !q || haystack.includes(q)
      return matchesFolder && matchesSearch
    })
    .slice()
    .sort((a, b) => {
      const byPath = a.relativePath.localeCompare(b.relativePath, undefined, { numeric: true, sensitivity: 'base' })
      if (byPath !== 0) return byPath
      return a.folder.localeCompare(b.folder, undefined, { sensitivity: 'base' })
    })
})

const selectedPdfCount = computed(() =>
  pdfWorkspace.value.files.filter(file => file.selected).length
)

const pdfProcessableCount = computed(() =>
  selectedPdfCount.value > 0 ? selectedPdfCount.value : readyToProcessFiles.value.length
)

const allVisibleSelected = computed(() =>
  visiblePdfFiles.value.length > 0 && visiblePdfFiles.value.every(file => file.selected)
)

const readyToProcessFiles = computed(() => {
  const files = visiblePdfFiles.value.filter(isPdfReadyToProcess)
  files.sort((a, b) => {
    const byInst = a.institutionLabel.localeCompare(b.institutionLabel, undefined, { sensitivity: 'base' })
    if (byInst !== 0) return byInst
    return a.relativePath.localeCompare(b.relativePath, undefined, { numeric: true, sensitivity: 'base' })
  })
  return files
})

const groupedReadyToProcess = computed(() =>
  groupFilesByInstitution(readyToProcessFiles.value)
)

const groupedPdfFiles = computed(() =>
  groupFilesByInstitution(visiblePdfFiles.value, { byPeriod: true })
)

const pdfCounts = computed(() => {
  const counts = { ok: 0, skipped: 0, error: 0 }
  for (const file of pdfWorkspace.value.files) {
    const status = getPdfStatusClass(file)
    if (status === 'ok') counts.ok++
    else if (status === 'skipped') counts.skipped++
    else if (status === 'error') counts.error++
  }
  return counts
})

const editableCategories = computed(() =>
  [...(store.categories || [])].sort((a, b) => a.category.localeCompare(b.category))
)
const domainRules  = computed(() => mailRules.value.filter(r => r.rule_type === 'sender_domain'))
const keywordRules = computed(() => mailRules.value.filter(r => r.rule_type === 'subject_keyword'))
const emailRules   = computed(() => mailRules.value.filter(r => r.rule_type === 'sender_email'))

async function loadMailRules() {
  try {
    mailRules.value = await api.getMailRules({ maxAgeMs: 0 })
  } catch (e) {
    mailRulesState.value = { loading: false, error: e.message, success: '' }
  }
}

async function addMailRule(ruleType) {
  const inputRef = ruleType === 'sender_email' ? mailRuleInputEmail
    : ruleType === 'sender_domain' ? mailRuleInputDomain
    : mailRuleInputKeyword
  const pattern = inputRef.value.trim()
  if (!pattern) return
  mailRulesState.value = { loading: true, error: null, success: '' }
  try {
    await api.addMailRule({ rule_type: ruleType, pattern, enabled: true })
    await loadMailRules()
    inputRef.value = ''
    mailRulesState.value = { loading: false, error: null, success: 'Rule added.' }
  } catch (e) {
    mailRulesState.value = { loading: false, error: e.message, success: '' }
  }
}

async function deleteMailRule(rule) {
  mailRulesState.value = { loading: true, error: null, success: '' }
  try {
    await api.deleteMailRule(rule.id)
    await loadMailRules()
    mailRulesState.value = { loading: false, error: null, success: 'Rule removed.' }
  } catch (e) {
    mailRulesState.value = { loading: false, error: e.message, success: '' }
  }
}

async function toggleMailRule(rule) {
  mailRulesState.value = { loading: true, error: null, success: '' }
  try {
    await api.patchMailRule(rule.id, { enabled: !rule.enabled })
    await loadMailRules()
    mailRulesState.value = { loading: false, error: null, success: '' }
  } catch (e) {
    mailRulesState.value = { loading: false, error: e.message, success: '' }
  }
}

const backupTiers = computed(() => {
  const status = backupState.value.status
  if (!status) return []
  return ['hourly', 'daily', 'weekly', 'monthly', 'manual']
    .map((key) => status[key])
    .filter(Boolean)
})

function resetCategoryEditor() {
  categorySelection.value = '__new__'
  categoryForm.value = makeEmptyCategoryForm()
  categoryEditorState.value = { loading: false, error: null, success: '' }
}

function onCategorySelectionChange(value) {
  categorySelection.value = value
  categoryEditorState.value = { loading: false, error: null, success: '' }
  if (value === '__new__') {
    categoryForm.value = makeEmptyCategoryForm()
    return
  }
  const match = editableCategories.value.find((category) => category.category === value)
  if (!match) {
    categoryForm.value = makeEmptyCategoryForm()
    return
  }
  categoryForm.value = {
    original_category: match.category,
    category: match.category,
    icon: match.icon || '',
    sort_order: match.sort_order ?? 99,
    is_recurring: !!match.is_recurring,
    monthly_budget: match.monthly_budget ?? '',
    category_group: match.category_group || '',
    subcategory: match.subcategory || '',
  }
}

function resetPdf() {
  pdf.value = EMPTY_PDF_STATE()
}

// ── Poll /api/pdf/local-status until done (max 3 min) ───────────────────────
let _pdfAbortController = null

onUnmounted(() => {
  _pdfAbortController?.abort()
})

async function pollStatus(jobId, timeoutMs = 180_000, signal = null) {
  const deadline = Date.now() + timeoutMs
  let transientErrors = 0
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new DOMException('Unmounted', 'AbortError')
    await new Promise(r => setTimeout(r, 2500))
    if (signal?.aborted) throw new DOMException('Unmounted', 'AbortError')
    try {
      const s = await api.pdfLocalStatus(jobId)
      transientErrors = 0
      if (s.status === 'done' || s.status === 'error') return s
    } catch (err) {
      if (err?.name === 'AbortError') throw err
      const message = String(err?.message || '')
      const isTransient = message.includes('502') || message.includes('503') || message.includes('504') || message.includes('Bridge unreachable')
      if (isTransient && transientErrors < 5) {
        transientErrors += 1
        continue
      }
      throw err
    }
  }
  throw new Error('Timed out after 3 min')
}

function inferInstitution(filename) {
  const upper = filename.toUpperCase()
  if (upper.includes('BNI_SEKURITAS')) return { key: 'bni-sekuritas', label: 'BNI Sekuritas' }
  if (upper.startsWith('BCA') || upper.includes('BCA_')) return { key: 'bca', label: 'BCA' }
  if (upper.includes('CIMB')) return { key: 'cimb-niaga', label: 'CIMB Niaga' }
  if (upper.includes('MAYBANK')) return { key: 'maybank', label: 'Maybank' }
  if (upper.includes('PERMATA')) return { key: 'permata', label: 'Permata' }
  if (upper.includes('IPOT') || upper.includes('INDO PREMIER')) return { key: 'ipot', label: 'IPOT' }
  if (upper.includes('STOCKBIT')) return { key: 'stockbit', label: 'Stockbit' }
  if (upper.includes('BNI')) return { key: 'bni', label: 'BNI' }
  if (upper.includes('SEKURITAS')) return { key: 'sekuritas', label: 'Sekuritas' }
  return { key: 'other', label: 'Other' }
}

function inferMonthBucket(filename) {
  const name = filename.toUpperCase()
  const monthNameMap = {
    JAN: '01', FEB: '02', MAR: '03', APR: '04', MAY: '05', JUN: '06',
    JUL: '07', AUG: '08', SEP: '09', OCT: '10', NOV: '11', DEC: '12',
    JANUARY: '01', FEBRUARY: '02', MARCH: '03', APRIL: '04', JUNE: '06',
    JULY: '07', AUGUST: '08', SEPTEMBER: '09', OCTOBER: '10', NOVEMBER: '11', DECEMBER: '12',
  }

  const candidates = [
    name.match(/(?:^|_)(\d{2})_(20\d{2})(?=\.|_|$)/),
    name.match(/(?:^|_)(20\d{2})_(\d{2})(?=\.|_|$)/),
    name.match(/(20\d{2})-(\d{2})-(\d{2})/),
    name.match(/(20\d{2})(\d{2})(\d{2})/),
    name.match(/(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(20\d{2})/),
    // Full or abbreviated month names with space/underscore, e.g. "February 26 0226"
    name.match(/(?:^|[\s_])(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|JAN|FEB|MAR|APR|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\s_]\d{1,2}[\s_](\d{2,4})(?=\.|[\s_]|$)/),
    // 4-digit MMYY code near end of filename, e.g. "...0226.pdf" where MM=02, YY=26
    name.match(/(\d{2})(\d{2})\.PDF$/),
  ].filter(Boolean)

  for (const match of candidates) {
    let year = ''
    let month = ''

    if (match[0].includes('-')) {
      year = match[1]
      month = match[2]
    } else if (monthNameMap[match[1]]) {
      month = monthNameMap[match[1]]
      year = match[2]
    } else if (match[1].length === 2 && match[2].length === 4) {
      month = match[1]
      year = match[2]
    } else {
      year = match[1]
      month = match[2]
    }

    // Normalize year to 4-digit: "26" → "2026", "0226" (MMYY) → "2026"
    if (/^\d{2}$/.test(year)) {
      year = '20' + year
    } else if (/^\d{4}$/.test(year) && !year.startsWith('20')) {
      // MMYY format: first 2 digits are month, last 2 are year
      const yPart = year.slice(2)
      if (Number(year.slice(0, 2)) >= 1 && Number(year.slice(0, 2)) <= 12) {
        year = '20' + yPart
      }
    }

    if (/^20\d{2}$/.test(year) && Number(month) >= 1 && Number(month) <= 12) {
      return {
        key: `${year}-${month}`,
        label: `${MONTH_LABELS[Number(month) - 1]} ${year}`,
        sortKey: `${year}-${month}`,
      }
    }
  }

  return {
    key: 'unknown-period',
    label: 'Unknown Period',
    sortKey: '0000-00',
  }
}

function mapWorkspaceFile(file, previous) {
  const institution = inferInstitution(file.filename)
  const monthBucket = inferMonthBucket(file.filename)
  const relativePath = file.relative_path || file.filename
  const lastSlash = relativePath.lastIndexOf('/')
  const relativeDir = lastSlash >= 0 ? relativePath.slice(0, lastSlash) : ''
  return {
    key: `${file.folder}/${relativePath}`,
    folder: file.folder,
    filename: file.filename,
    relativePath,
    relativeDir,
    sizeKb: file.size_kb,
    mtime: file.mtime,
    lastProcessedAt: file.last_processed_at,
    lastStatus: file.last_status,
    lastError: file.last_error || '',
    selected: previous?.selected || false,
    processingState: previous?.processingState || null,
    processingMeta: previous?.processingMeta || '',
    institutionKey: institution.key,
    institutionLabel: institution.label,
    monthKey: `${institution.key}:${monthBucket.key}`,
    monthLabel: monthBucket.label,
    monthSortKey: monthBucket.sortKey,
  }
}

async function loadPdfWorkspace() {
  pdfWorkspace.value.loading = true
  pdfWorkspace.value.error = null
  pdf.value.fatalError = null
  try {
    const res = await api.pdfLocalWorkspace()
    const previousByKey = new Map(pdfWorkspace.value.files.map(file => [file.key, file]))
    pdfWorkspace.value.files = (res.files || []).map(file =>
      mapWorkspaceFile(file, previousByKey.get(`${file.folder}/${file.relative_path || file.filename}`))
    )
    pdfWorkspace.value.loaded = true
  } catch (err) {
    pdfWorkspace.value.error = err.message
  } finally {
    pdfWorkspace.value.loading = false
  }
}

async function togglePdfWorkspace() {
  showPdfWorkspace.value = !showPdfWorkspace.value
  if (showPdfWorkspace.value && !pdfWorkspace.value.loaded) {
    await loadPdfWorkspace()
  }
}

function clearPdfSelection() {
  pdfWorkspace.value.files.forEach(file => { file.selected = false })
}

function toggleVisibleSelection(checked) {
  visiblePdfFiles.value.forEach(file => { file.selected = checked })
}

function isInstitutionExpanded(key) {
  return pdfWorkspace.value.search.trim() !== '' || Boolean(pdfExpanded.value.institutions[key])
}

function isMonthExpanded(key) {
  return pdfWorkspace.value.search.trim() !== '' || Boolean(pdfExpanded.value.months[key])
}

function toggleInstitutionGroup(key) {
  pdfExpanded.value.institutions[key] = !pdfExpanded.value.institutions[key]
}

function toggleMonthGroup(key) {
  pdfExpanded.value.months[key] = !pdfExpanded.value.months[key]
}

function applyPdfRunResult(file, final) {
  file.lastProcessedAt = final.created_at || new Date().toISOString()
  const log = String(final.log || '').toLowerCase()
  if (final.status === 'error') {
    file.processingState = 'error'
    file.processingMeta = truncateText(final.error || 'Parser error')
    file.lastStatus = 'error'
    file.lastError = final.error || 'Parser error'
    return
  }

  if (final.status === 'partial') {
    file.processingState = null
    file.processingMeta = truncateText(final.error || 'Partial success — some secondary writes failed')
    file.lastStatus = 'partial'
    file.lastError = final.error || ''
    return
  }

  file.lastStatus = 'done'
  file.lastError = ''

  if (log.includes('duplicate') || log.includes('skipped') || log.includes('already imported')) {
    file.processingState = 'skipped'
    file.processingMeta = 'Already imported'
    return
  }

  file.processingState = 'ok'
  const match = String(final.log || '').match(/(?:rows added|upserted|bond|fund)[^\n]*/i)
  file.processingMeta = match ? truncateText(match[0].trim(), 80) : 'Imported'
}

async function runPdfPreflight() {
  preflightState.value = { loading: true, error: null, errors: [], warnings: [] }
  try {
    const result = await api.pdfPreflight({ forceFresh: true })
    preflightState.value = {
      loading: false,
      error: null,
      errors: result.errors || [],
      warnings: result.warnings || [],
    }
    return result.ok === true
  } catch (err) {
    preflightState.value = {
      loading: false,
      error: err.message,
      errors: [err.message],
      warnings: [],
    }
    return false
  }
}

async function processSelectedPdfs() {
  // ── Pre-flight validation ──
  const preflightOk = await runPdfPreflight()
  if (!preflightOk) return
  pdfProcessWarning.value = ''

  const explicitSelection = pdfWorkspace.value.files
    .filter(file => file.selected)
    .map(file => ({
      key: file.key,
      folder: file.folder,
      relativePath: file.relativePath,
      filename: file.filename,
    }))
  const selected = explicitSelection.length > 0
    ? explicitSelection
    : readyToProcessFiles.value.map(file => ({
        key: file.key,
        folder: file.folder,
        relativePath: file.relativePath,
        filename: file.filename,
      }))
  if (selected.length === 0) return
  console.log(`[PDF] Processing ${selected.length} file(s):`, selected.map(f => f.filename))

  _pdfAbortController?.abort()
  _pdfAbortController = new AbortController()
  const { signal } = _pdfAbortController

  resetPdf()
  pdf.value.phase = 'processing'
  pdf.value.total = selected.length

  const queued = []
  for (const item of selected) {
    if (signal.aborted) break
    const file = pdfWorkspace.value.files.find(candidate => candidate.key === item.key)
    if (!file) continue

    file.processingState = 'processing'
    file.processingMeta = ''
    pdf.value.current = item.filename

    try {
      const res = await api.processLocalPdf(item.folder, item.relativePath)
      const jobId = res.job_id
      if (!jobId) throw new Error('No job_id returned')
      queued.push({ ...item, jobId })
      console.log(`[PDF] Queued: ${item.filename} → job ${jobId}`)
    } catch (err) {
      if (err?.name === 'AbortError') break
      console.warn(`[PDF] Failed to queue: ${item.filename} — ${err.message}`)
      file.processingState = 'error'
      file.processingMeta = truncateText(err.message)
      file.lastStatus = 'error'
      file.lastError = err.message
      pdf.value.processed += 1
    }
  }

  for (const item of queued) {
    if (signal.aborted) break
    const file = pdfWorkspace.value.files.find(candidate => candidate.key === item.key)
    if (!file) {
      pdf.value.processed += 1
      continue
    }

    file.processingState = 'processing'
    file.processingMeta = ''
    pdf.value.current = item.filename

    try {
      const final = await pollStatus(item.jobId, 180_000, signal)
      applyPdfRunResult(file, final)
      console.log(`[PDF] Completed: ${item.filename} → ${final.status}`)
    } catch (err) {
      if (err?.name === 'AbortError') break
      console.warn(`[PDF] Poll failed: ${item.filename} — ${err.message}`)
      file.processingState = 'error'
      file.processingMeta = truncateText(err.message)
      file.lastStatus = 'error'
      file.lastError = err.message
    } finally {
      pdf.value.processed += 1
    }
  }

  pdf.value.phase = 'idle'
  pdf.value.current = ''

  // Tally per-file outcomes for the run summary
  const tally = { ok: 0, error: 0, partial: 0 }
  for (const item of selected) {
    const file = pdfWorkspace.value.files.find(candidate => candidate.key === item.key)
    if (!file) continue
    const cls = getPdfStatusClass(file)
    if (cls === 'ok') tally.ok++
    else if (cls === 'partial') tally.partial++
    else if (cls === 'error') tally.error++
  }
  pdf.value.summary = tally

  console.log(`[PDF] All done. ${pdf.value.processed}/${pdf.value.total} processed. Summary:`, tally)
  if (pdf.value.total > 0 && pdf.value.processed === 0) {
    pdfProcessWarning.value = `Preflight passed but 0 of ${pdf.value.total} file(s) were processed. Check console for details.`
    console.warn('[PDF] Warning: preflight OK but no files were processed — possible silent failure.')
  }
  await loadPdfWorkspace()
}

async function saveCategoryEditor() {
  categoryEditorState.value = { loading: true, error: null, success: '' }
  try {
    const payload = {
      original_category: categoryForm.value.original_category,
      category: categoryForm.value.category.trim(),
      icon: categoryForm.value.icon.trim(),
      sort_order: Number(categoryForm.value.sort_order || 0),
      monthly_budget: categoryForm.value.monthly_budget === '' ? null : Number(categoryForm.value.monthly_budget),
      category_group: categoryForm.value.category_group.trim(),
      subcategory: categoryForm.value.subcategory.trim(),
      is_recurring: !!categoryForm.value.is_recurring,
    }
    const saved = await api.saveCategoryDefinition(payload)
    await store.loadCategories({ forceFresh: true })
    categorySelection.value = saved.category
    categoryForm.value.original_category = saved.category
    categoryForm.value.category = saved.category
    categoryEditorState.value = {
      loading: false,
      error: null,
      success: `Saved ${saved.category}`,
    }
  } catch (e) {
    categoryEditorState.value = { loading: false, error: e.message, success: '' }
  }
}

async function doImport() {
  importState.value = { loading: true, result: null, error: null }
  try {
    const res = await api.importData({
      dry_run:   importOpts.value.dry_run,
      overwrite: importOpts.value.overwrite,
    })
    importState.value.result = res.queued ? { status: 'queued' } : res
    if (!res.queued && !importOpts.value.dry_run) {
      await store.loadHealth({ forceFresh: true })
      await loadBackupStatus()
    }
  } catch (e) {
    importState.value.error = e.message
  } finally {
    importState.value.loading = false
  }
}

function fmtRelativeTime(iso) {
  if (!iso) return 'Never'
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  const diff = Math.floor((Date.now() - d.getTime()) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function baseName(path) {
  if (!path) return '—'
  return path.split('/').pop()
}

function backupStatusLabel(status) {
  if (status === 'ok') return 'OK'
  if (status === 'due') return 'Due'
  return 'Missing'
}

async function loadBackupStatus() {
  try {
    backupState.value.status = await api.backupStatus({ forceFresh: true })
    backupState.value.error = null
  } catch (e) {
    backupState.value.error = e.message
  }
}

async function doManualBackup() {
  const previousStatus = backupState.value.status
  backupState.value = { loading: true, error: null, status: previousStatus, result: null }
  try {
    const result = await api.manualBackup()
    backupState.value = {
      loading: false,
      error: null,
      status: result.status || previousStatus,
      result,
    }
    await loadBackupStatus()
  } catch (e) {
    backupState.value = { loading: false, error: e.message, status: previousStatus, result: null }
  }
}

function formatPipelineSummary(result) {
  if (!result) return 'No runs yet'
  return `${result.files_ok || 0} ok, ${result.files_failed || 0} failed, ${result.files_skipped || 0} skipped, ${result.import_new_tx || 0} imported`
}

async function refreshMobileCache() {
  const previousDoneAt = refreshCacheState.value.doneAt
  refreshCacheState.value = { loading: true, error: null, doneAt: previousDoneAt }
  try {
    await api.refreshReferenceData()
    await store.bootstrap({ forceFresh: true })
    refreshCacheState.value = {
      loading: false,
      error: null,
      doneAt: new Date().toLocaleString(),
    }
  } catch (e) {
    refreshCacheState.value = {
      loading: false,
      error: e.message,
      doneAt: previousDoneAt,
    }
  }
}

async function loadPipelineStatus() {
  try {
    pipelineState.value.status = await api.pipelineStatus({ forceFresh: true })
    pipelineState.value.error = null
  } catch (e) {
    pipelineState.value.error = e.message
  }
}

async function runPipeline() {
  pipelineState.value.loading = true
  try {
    const res = await api.runPipeline()
    if (res.queued) {
      pipelineState.value.status = { status: 'queued' }
      pipelineState.value.error = null
      return
    }
    if (res.status === 'already_running') {
      await loadPipelineStatus()
      return
    }
    await loadPipelineStatus()
    if (!importOpts.value.dry_run) await store.loadHealth({ forceFresh: true })
  } catch (e) {
    pipelineState.value.error = e.message
  } finally {
    pipelineState.value.loading = false
  }
}

// ── NAS sync ──────────────────────────────────────────────────────────────────
function fmtNasSync(iso) {
  return fmtRelativeTime(iso)
}

async function loadNasSyncStatus() {
  try {
    nasSyncStatus.value = await api.nasSyncStatus({ forceFresh: true })
  } catch {}
}

async function doNasSync() {
  nasSyncState.value = { loading: true, result: null }
  try {
    const result = await api.nasSync()
    nasSyncState.value = { loading: false, result }
    if (result.ok) nasSyncStatus.value.last_synced_at = result.synced_at
  } catch (e) {
    nasSyncState.value = { loading: false, result: { ok: false, error: e.message } }
  }
}

async function loadHouseholdSettings() {
  householdState.value = {
    ...householdState.value,
    loading: true,
    error: null,
    unavailableReason: '',
    success: '',
  }
  try {
    const payload = await api.householdSettings({ forceFresh: true })
    normalizeHouseholdState(payload)
  } catch (e) {
    householdState.value = {
      ...householdState.value,
      loading: false,
      error: e.message,
      success: '',
    }
  }
}

async function saveHouseholdTransactionCategory(txn) {
  txn.saving = true
  householdState.value.success = ''
  try {
    const updated = await api.updateHouseholdTransactionCategory(txn.id, { category_code: txn.draftCategory })
    txn.category_code = updated.category_code || txn.draftCategory
    txn.draftCategory = txn.category_code
    householdState.value.success = `Updated household expense #${txn.id}`
  } catch (e) {
    householdState.value.error = e.message
  } finally {
    txn.saving = false
  }
}

async function createHouseholdCategory() {
  householdState.value.error = null
  householdState.value.success = ''
  try {
    await api.createHouseholdCategory({
      code: householdCategoryForm.value.code.trim(),
      label_id: householdCategoryForm.value.label_id.trim(),
      sort_order: Number(householdCategoryForm.value.sort_order || 99),
    })
    householdCategoryForm.value = { code: '', label_id: '', sort_order: 99 }
    await loadHouseholdSettings()
    householdState.value.success = 'Created household category'
  } catch (e) {
    householdState.value.error = e.message
  }
}

async function saveHouseholdCategory(category) {
  category.saving = true
  householdState.value.error = null
  householdState.value.success = ''
  try {
    await api.updateHouseholdCategory(category.originalCode, {
      code: category.draftCode.trim(),
      label_id: category.draftLabel.trim(),
      sort_order: Number(category.draftSortOrder || 99),
    })
    await loadHouseholdSettings()
    householdState.value.success = `Updated household category ${category.originalCode}`
  } catch (e) {
    householdState.value.error = e.message
  } finally {
    category.saving = false
  }
}

async function removeHouseholdCategory(category) {
  category.deleting = true
  householdState.value.error = null
  householdState.value.success = ''
  try {
    await api.deleteHouseholdCategory(category.originalCode)
    await loadHouseholdSettings()
    householdState.value.success = `Removed household category ${category.originalCode}`
  } catch (e) {
    householdState.value.error = e.message
  } finally {
    category.deleting = false
  }
}

async function saveHouseholdCashPool(pool) {
  pool.saving = true
  householdState.value.success = ''
  try {
    const updated = await api.updateHouseholdCashPool(pool.id, {
      adjustment_amount: Number(pool.adjustmentInput || 0),
      notes: pool.notesInput || '',
    })
    pool.remaining_amount = updated.remaining_amount
    pool.status = updated.status || pool.status
    pool.notes = updated.notes || ''
    pool.notesInput = pool.notes
    pool.adjustmentInput = ''
    householdState.value.success = `Adjusted ${pool.name}`
  } catch (e) {
    householdState.value.error = e.message
  } finally {
    pool.saving = false
  }
}

onMounted(async () => {
  await store.loadHealth({ forceFresh: true })
  await store.loadCategories({ forceFresh: true })
  await loadBackupStatus()
  await loadPipelineStatus()
  await loadNasSyncStatus()
  await loadHouseholdSettings()
  await loadMailRules()

  // ── Guard: reset active section if invalid for current mode ──
  const RESTRICTED = ['import-xlsx','pdf-pipeline','process-pdfs','backup','nas-sync','household']
  if (store.isReadOnly && RESTRICTED.includes(activeSection.value)) {
    activeSection.value = 'dashboard-range'
  }
  if (activeSection.value === 'nas-sync' && !nasSyncStatus.value.configured) {
    activeSection.value = 'dashboard-range'
  }
})
</script>

<style scoped>
.api-status-sv { display: flex; align-items: center; gap: 8px; }
.api-status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.api-status-dot--ok { background: #22c55e; box-shadow: 0 0 4px rgba(34,197,94,0.5); }
.api-status-dot--err { background: #ef4444; box-shadow: 0 0 4px rgba(239,68,68,0.5); }
.settings-head-icon,
.setting-title-icon,
.inline-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--primary-deep);
  flex-shrink: 0;
}
.settings-head-icon {
  width: 16px;
  height: 16px;
  margin-right: 8px;
  vertical-align: middle;
}
.setting-title-icon {
  width: 15px;
  height: 15px;
  margin-right: 8px;
}
.inline-icon {
  width: 13px;
  height: 13px;
  margin-right: 6px;
  vertical-align: middle;
}
.settings-head-icon :deep(svg) {
  width: 16px;
  height: 16px;
}
.setting-title-icon :deep(svg) {
  width: 15px;
  height: 15px;
}
.inline-icon :deep(svg) {
  width: 13px;
  height: 13px;
}

.mail-rules-section { margin-top: 16px; }
.mail-rules-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .05em;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.mail-rules-empty { font-size: 13px; color: var(--text-muted); padding: 4px 0 8px; }
.mail-rule-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--border-subtle, rgba(0,0,0,.06));
}
.mail-rule-pattern {
  font-size: 13px;
  font-family: monospace;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mail-rule-pattern--disabled { opacity: .4; text-decoration: line-through; }
.mail-rule-actions { display: flex; gap: 4px; flex-shrink: 0; }
.mail-rule-delete { color: var(--text-muted); }
.mail-rule-delete:hover { color: #ef4444; }
.mail-rule-add-row {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.mail-rule-add-row .form-input { flex: 1; min-width: 0; }

.settings-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 14px;
  align-items: start;
}

.settings-grid > * {
  min-width: 0;
}

.more-nav-grid {
  display: grid;
  gap: 10px;
  margin-top: 10px;
}

.more-nav-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--card-bg) 88%, transparent);
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s ease, background 0.15s ease, transform 0.15s ease;
}

.more-nav-card:active {
  transform: translateY(1px);
}

.more-nav-card:hover {
  border-color: var(--primary);
  background: var(--primary-dim);
}

.more-nav-card__icon {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--primary-deep);
  flex-shrink: 0;
}

.more-nav-card__icon :deep(svg) {
  width: 18px;
  height: 18px;
}

.more-nav-card__body {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}

.more-nav-card__title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
}

.more-nav-card__desc {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.4;
}

.more-nav-card__chevron {
  font-size: 14px;
  color: var(--text-muted);
  flex-shrink: 0;
}

.settings-section-label {
  grid-column: 1 / -1;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-top: 2px;
}

.household-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.household-pane {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px;
  background: color-mix(in srgb, var(--card-bg) 82%, transparent);
}

.household-pane--wide {
  grid-column: 1 / -1;
}

.household-pane__title {
  font-size: 13px;
  font-weight: 800;
  margin-bottom: 10px;
}

.household-item {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px;
  background: var(--card-bg);
}

.household-item + .household-item {
  margin-top: 10px;
}

.household-item__head,
.household-item__controls {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.household-item__controls {
  margin-top: 10px;
  align-items: center;
}

.household-item__controls--compact {
  margin-top: 0;
  justify-content: flex-end;
}

.household-item__title {
  font-size: 13px;
  font-weight: 700;
}

.household-item__meta,
.household-empty {
  font-size: 12px;
  color: var(--text-muted);
}

.household-category-create,
.household-category-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
}

.household-category-create {
  margin-bottom: 12px;
}

.household-action-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.household-delete-btn {
  color: #ef4444;
}

.household-chip,
.about-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  background: rgba(37, 99, 235, 0.12);
  color: #2563eb;
}

.household-pool-grid,
.about-stack {
  display: grid;
  gap: 10px;
}

.about-stack {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.7;
}

.about-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.about-meta-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-muted);
}

.about-pill--readonly {
  width: fit-content;
}

@media (max-width: 720px) {
  .settings-grid {
    grid-template-columns: 1fr;
  }

  .household-item__controls,
  .about-meta-row {
    flex-direction: column;
    align-items: stretch;
  }
}

.category-editor-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
}

.category-editor-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.category-editor-actions {
  margin-top: 12px;
}

.backup-tier-list {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.backup-tier-card {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px;
  background: var(--card-bg);
}

.backup-tier-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.backup-tier-title {
  font-size: 13px;
  font-weight: 700;
}

.backup-tier-sub,
.backup-tier-meta,
.backup-tier-file {
  font-size: 12px;
  color: var(--text-muted);
}

.backup-tier-meta,
.backup-tier-file {
  margin-top: 4px;
}

.backup-tier-file {
  word-break: break-word;
}

.backup-tier-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.backup-tier-pill--ok {
  background: #dcfce7;
  color: #166534;
}

.backup-tier-pill--due {
  background: #fef3c7;
  color: #92400e;
}

.backup-tier-pill--missing {
  background: #fee2e2;
  color: #991b1b;
}

/* ── PDF section ──────────────────────────────────────────────────────────── */

/* Wrapper around the button so title tooltip works when button is :disabled */
.pdf-btn-wrapper {
  display: inline-flex;
}

.pipeline-grid {
  display: grid;
  gap: 12px;
}

.setting-row-range {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.range-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.range-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.range-select {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--card);
  color: var(--text);
  font: inherit;
}

@media (max-width: 640px) {
  .setting-row-range {
    grid-template-columns: 1fr;
  }
}

/* Visual greyed-out look for non-Mac (disabled button already prevents clicks) */
.btn-disabled-look {
  opacity: 0.45;
  cursor: not-allowed;
}

.pdf-unavail-note {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-muted, #888);
  text-align: center;
}

/* Progress bar */
.pdf-progress-bar-wrap {
  height: 4px;
  background: var(--bg, #f0f0f0);
  border-radius: 2px;
  margin-top: 10px;
  overflow: hidden;
}
.pdf-progress-bar {
  height: 100%;
  background: var(--accent, #4e8fff);
  border-radius: 2px;
  transition: width 0.3s ease;
}

/* Currently processing filename */
.pdf-current-file {
  margin-top: 5px;
  font-size: 11px;
  color: var(--text-muted, #888);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Summary badge row */
.pdf-summary-bar {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.pdf-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 600;
}
.pdf-badge-ok   { background: rgba(52,199,89,.15);  color: #1a7a3a; }
.pdf-badge-skip { background: rgba(255,204,0,.18);  color: #7a5c00; }
.pdf-badge-err  { background: rgba(255,59,48,.12);  color: #a0200c; }
.pdf-badge-pend { background: rgba(120,120,128,.1); color: var(--text-muted,#888); }

/* Workspace */
.pdf-workspace {
  margin-top: 12px;
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 10px;
  padding: 12px;
  background:
    linear-gradient(180deg, rgba(17,27,43,0.96) 0%, rgba(11,19,33,0.98) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.04),
    0 18px 36px rgba(5,10,18,0.28);
  backdrop-filter: blur(16px);
}

.pdf-workspace-toolbar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.pdf-search,
.pdf-filter {
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 8px;
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.92);
  font-size: 12px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}

.pdf-search::placeholder {
  color: rgba(255,255,255,0.40);
}

.pdf-search:focus,
.pdf-filter:focus {
  outline: none;
  border-color: rgba(96,165,250,0.55);
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.05),
    0 0 0 3px rgba(59,130,246,0.14);
}

.pdf-search {
  flex: 1 1 220px;
}

.pdf-filter {
  flex: 0 0 auto;
}

.pdf-empty-state {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: rgba(255,255,255,0.62);
  font-size: 12px;
  text-align: center;
}

.pdf-groups {
  margin-top: 12px;
}

.pdf-groups-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.pdf-master-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: rgba(255,255,255,0.72);
}

.pdf-group-card,
.pdf-month-card {
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 10px;
  background: rgba(255,255,255,0.02);
}

.pdf-group-card + .pdf-group-card {
  margin-top: 12px;
}

.pdf-month-list {
  padding: 0 10px 10px;
}

.pdf-month-card + .pdf-month-card {
  margin-top: 8px;
}

.pdf-group-header,
.pdf-month-header {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  background: transparent;
  border: 0;
  color: rgba(255,255,255,0.94);
  cursor: pointer;
  text-align: left;
}

.pdf-month-header {
  padding: 12px 14px;
}

.pdf-group-header:hover,
.pdf-month-header:hover {
  background: rgba(255,255,255,0.03);
}

.pdf-group-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  font-weight: 700;
}

.pdf-group-chevron {
  width: 12px;
  color: rgba(147,197,253,0.9);
  flex: 0 0 auto;
}

.pdf-group-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
  color: rgba(255,255,255,0.72);
}

.pdf-table-wrap {
  margin: 0 10px 10px;
  overflow: auto;
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 8px;
  background: rgba(7,14,25,0.82);
}

/* PdfFileTable.vue owns its own grid + chip styles via scoped <style scoped> */

.pdf-detail-cell {
  max-width: 240px;
  color: rgba(255,255,255,0.62);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pdf-desktop-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pdf-desktop-tools {
  margin-top: 12px;
}

.pdf-workspace-footer {
  margin-top: 12px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.pdf-workspace-footer-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pdf-selection-note {
  font-size: 12px;
  color: rgba(255,255,255,0.62);
}

.pdf-workspace :deep(input[type="checkbox"]) {
  accent-color: #60a5fa;
}

/* ── Two-column layout ─────────────────────────────────────────────────── */
.pdf-layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 20px;
  align-items: start;
}

.pdf-controls {
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pdf-controls-toolbar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: stretch;
}

.pdf-controls-footer {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid rgba(255,255,255,0.08);
}

.pdf-controls-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pdf-controls-actions .btn {
  width: 100%;
  justify-content: center;
}

.pdf-progress-panel {
  padding: 10px 12px;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 8px;
  background: rgba(255,255,255,0.02);
}

.pdf-content {
  min-width: 0;
}

.pdf-content .pdf-groups {
  margin-top: 0;
}

@media (max-width: 1024px) {
  .pdf-layout {
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .pdf-controls {
    position: static;
  }

  .pdf-controls-toolbar {
    flex-direction: row;
    flex-wrap: wrap;
  }

  .pdf-controls-toolbar .btn {
    flex: 0 0 auto;
    width: auto;
  }
}

@media (max-width: 820px) {
  .pdf-workspace {
    padding: 10px;
  }

  .pdf-group-header,
  .pdf-month-header {
    flex-direction: column;
    align-items: stretch;
  }

  .pdf-group-meta {
    justify-content: space-between;
  }

  .pdf-row {
    grid-template-columns: minmax(0, 1fr) 110px 80px;
  }
}

@media (min-width: 1024px) {
  .settings-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    align-items: start;
  }
}

/* ── Two-column desktop shell ──────────────────────────────────────────── */
.settings-page--desktop {
  display: grid;
  grid-template-columns: 240px 1fr;
  align-items: start;
  min-height: 100%;
}

.settings-content { min-width: 0; }

/* ── Left nav — pixel-match DesktopSidebar.vue ─────────────────────────── */
.settings-sub-nav {
  width: 240px;
  position: sticky;
  top: 0;
  padding: 16px 10px 20px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-right: 1px solid rgba(136,189,242,0.16);
  min-height: calc(100vh - 48px);
  margin-right: 24px;
}

.settings-sub-nav__title {
  font-size: 15px;
  font-weight: 800;
  color: var(--text);
  letter-spacing: -0.01em;
  padding: 4px 12px 12px;
}

.settings-sub-nav__group-label {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-muted);
  padding: 8px 12px 4px;
}

.settings-sub-nav__divider {
  height: 1px;
  background: rgba(136,189,242,0.12);
  margin: 6px 12px;
}

/* mirrors .desktop-sidebar__link exactly */
.settings-sub-nav__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  border: none;
  background: transparent;
  color: rgba(255,255,255,0.70);
  font-size: 14px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
  transition: all 0.12s ease;
  width: 100%;
}
.settings-sub-nav__item:hover {
  background: rgba(136,189,242,0.12);
  color: #fff;
}
.settings-sub-nav__item.is-active {
  background: linear-gradient(180deg, rgba(136,189,242,0.22), rgba(106,137,167,0.15));
  color: #fff;
  box-shadow: inset 0 0 0 1px rgba(189,221,252,0.22);
}

/* mirrors .sidebar-icon exactly */
.settings-sub-nav__icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--primary-deep);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  opacity: 0.75;
  transition: opacity 0.12s;
}
.settings-sub-nav__icon :deep(svg) { width: 16px; height: 16px; }
.settings-sub-nav__item:hover .settings-sub-nav__icon,
.settings-sub-nav__item.is-active .settings-sub-nav__icon {
  opacity: 1;
  color: var(--primary);
}

/* Force single card per row in right panel (existing grid is 1fr 1fr on desktop) */
.settings-page--desktop .settings-grid {
  grid-template-columns: 1fr !important;
}

/* ── Section switch fade (140ms, snappy) ────────────────────────────────── */
.settings-fade-enter-active,
.settings-fade-leave-active { transition: opacity 0.14s ease, transform 0.14s ease; }
.settings-fade-enter-from,
.settings-fade-leave-to { opacity: 0; transform: translateY(4px); }

/* Ready-to-process bank sub-label */
.rtp-bank-label {
  padding: 6px 12px;
  font-size: 12px;
  color: var(--text-dim);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .5px;
}
.preflight-errors {
  margin: 8px 0;
  padding: 10px 12px;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 6px;
  font-size: 12.5px;
}
.preflight-error-item {
  color: #fca5a5;
  margin: 2px 0;
}
.preflight-warnings {
  margin: 8px 0;
  padding: 10px 12px;
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: 6px;
  font-size: 12.5px;
}
.preflight-warning-item {
  color: #fcd34d;
  margin: 2px 0;
}
</style>
