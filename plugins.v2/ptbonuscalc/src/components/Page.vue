<template>
  <div class="page">
    <div v-if="!sites.length && !loading" class="text-center pa-4 text-body-1">
      <template v-if="errorMessage">{{ errorMessage }}</template>
      <template v-else>暂无做种数据或未配置站点。请先在插件配置中勾选「显示站点」并保存，再刷新站点用户数据。</template>
    </div>
    <div v-else>
      <div v-if="errorMessage" class="text-caption text-error mb-2">{{ errorMessage }}</div>
      <div v-if="successMessage" class="text-caption text-success mb-2">{{ successMessage }}</div>
      <v-expansion-panels v-model="expandedDomains" multiple>
        <v-expansion-panel v-for="site in sites" :key="site.domain" :value="site.domain">
          <v-expansion-panel-title class="bg-blue-lighten-5">
            <span class="flex-grow-1">{{ site.site_name }}（<template v-if="loadingDomain === site.domain">加载中…</template><template v-else>{{ site.torrents.length || site.total_seed_count || 0 }} 个做种</template>，时魔 {{ formatBonus(site.total_bonus_per_hour) }}）</span>
            <span v-if="hasUnsavedChanges(site.domain)" class="text-caption text-warning mr-2">未保存</span>
            <v-btn size="small" variant="tonal" color="primary" class="mr-1" :loading="loadingDomain === site.domain" @click.stop="refreshSite(site.domain)">刷新</v-btn>
            <v-btn size="small" variant="tonal" color="success" class="mr-1" :loading="savingDomain === site.domain" @click.stop="saveSiteData(site.domain)">保存</v-btn>
          </v-expansion-panel-title>
          <v-expansion-panel-text>
            <div v-if="loadingDomain === site.domain" class="pa-4 text-center text-body-2 text-medium-emphasis">加载中…</div>
            <div v-else-if="site.torrents.length" class="site-tables-wrapper">
              <div class="tables-header-row">
                <div class="table-panel table-panel-left">
                  <div class="table-header-fixed" @scroll="onHeaderScroll($event, 'left')">
                    <table class="seed-table text-caption">
                      <colgroup><col class="col-num"><col class="col-name"><col class="col-size"><col class="col-num"><col class="col-num"></colgroup>
                      <thead>
                        <tr>
                          <th class="text-end">#</th>
                          <th class="text-start col-name">种子名</th>
                          <th class="text-end">大小</th>
                          <th class="text-end">做种</th>
                          <th class="text-end">魔力/h</th>
                        </tr>
                      </thead>
                    </table>
                  </div>
                </div>
                <div class="table-panel table-panel-right">
                  <div class="table-header-fixed" @scroll="onHeaderScroll($event, 'right')">
                    <table class="seed-table text-caption">
                      <colgroup><col class="col-num"><col class="col-name"><col class="col-size"><col class="col-tracker"><col class="col-hash"><col class="col-state"><col class="col-size"><col class="col-size"><col class="col-num"><col class="col-time"></colgroup>
                      <thead>
                        <tr>
                          <th class="text-end">#</th>
                          <th class="text-start col-name">名称</th>
                          <th class="text-end">大小</th>
                          <th class="text-start">Tracker</th>
                          <th class="text-start">Hash</th>
                          <th class="text-start">状态</th>
                          <th class="text-end">上传</th>
                          <th class="text-end">下载</th>
                          <th class="text-end">分享率</th>
                          <th class="text-start">添加时间</th>
                        </tr>
                      </thead>
                    </table>
                  </div>
                </div>
              </div>
              <div class="tables-body-scroll">
                <div class="tables-body-inner">
                  <div class="table-body-col table-panel-left" @scroll="onBodyColScroll($event, 'left')">
                    <table class="seed-table text-caption">
                      <colgroup><col class="col-num"><col class="col-name"><col class="col-size"><col class="col-num"><col class="col-num"></colgroup>
                      <tbody>
                        <tr
                          v-for="(r, idx) in site.torrents"
                          :key="'L-' + site.domain + '-' + (r.torrent_key || idx)"
                          class="seed-row"
                          :class="{ 'row-associated-hover': isRowHovered(site.domain, idx) }"
                          @mouseenter="hoveredRow = { siteDomain: site.domain, index: idx }"
                          @mouseleave="hoveredRow = null"
                        >
                          <td class="text-end">{{ idx + 1 }}</td>
                          <td class="text-start col-name" :title="r.name">
                            <a
                              v-if="seedDetailUrl(site, r) !== '#'"
                              :href="seedDetailUrl(site, r)"
                              target="_blank"
                              rel="noopener noreferrer"
                              class="seed-name-link"
                              @click.stop
                            >{{ r.name }}</a>
                            <span v-else>{{ r.name }}</span>
                          </td>
                          <td class="text-end">{{ formatSize(r.size) }}</td>
                          <td class="text-end">{{ r.seeders }}</td>
                          <td class="text-end">{{ formatBonus(r.bonus_per_hour) }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div class="table-body-col table-panel-right" @scroll="onBodyColScroll($event, 'right')">
                    <table class="seed-table text-caption">
                      <colgroup><col class="col-num"><col class="col-name"><col class="col-size"><col class="col-tracker"><col class="col-hash"><col class="col-state"><col class="col-size"><col class="col-size"><col class="col-num"><col class="col-time"></colgroup>
                      <tbody>
                        <tr
                          v-for="(r, idx) in site.torrents"
                          :key="'R-' + site.domain + '-' + (r.torrent_key || idx)"
                          class="seed-row"
                          :class="{ 'row-associated-hover': isRowHovered(site.domain, idx), 'row-unmatched': !r.downloader_hash }"
                          @mouseenter="hoveredRow = { siteDomain: site.domain, index: idx }"
                          @mouseleave="hoveredRow = null"
                        >
                          <td class="text-end">{{ idx + 1 }}</td>
                          <td class="text-start col-name cell-match">
                            <template v-if="!r.downloader_hash">
                              <v-select
                                v-if="getAvailableCandidatesForSite(site).length > 0"
                                density="compact"
                                hide-details
                                variant="outlined"
                                placeholder="选择匹配的下载器种子"
                                :items="getAvailableCandidatesForSite(site)"
                                item-title="label"
                                item-value="hash"
                                @update:model-value="(hash: string) => onSelectMatch(site.domain, idx, hash)"
                              />
                              <span v-else class="text-medium-emphasis text-caption">无可选数据</span>
                            </template>
                            <template v-else>
                              <div class="cell-name-with-action">
                                <span v-if="r.suggested_match" class="suggested-tag">建议</span>
                                <span class="cell-name-text" :title="(r.downloader_torrent_name || r.downloader_name || '')">{{ r.downloader_torrent_name || r.downloader_name || '—' }}</span>
                                <v-btn size="x-small" variant="text" color="error" class="cell-cancel-btn" @click="onCancelMatch(site.domain, idx)">取消匹配</v-btn>
                              </div>
                            </template>
                          </td>
                          <td class="text-end">{{ formatSize(r.downloader_size) }}</td>
                          <td class="text-start col-tracker">{{ r.downloader_tracker || '—' }}</td>
                          <td class="text-start font-mono col-hash">{{ r.downloader_hash || '—' }}</td>
                          <td class="text-start">{{ r.downloader_state || '—' }}</td>
                          <td class="text-end">{{ formatSize(r.downloader_uploaded) }}</td>
                          <td class="text-end">{{ formatSize(r.downloader_downloaded) }}</td>
                          <td class="text-end">{{ formatRatio(r.downloader_ratio) }}</td>
                          <td class="text-start text-caption">{{ formatAddedAt(r.downloader_added_at) }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="pa-4 text-center text-body-2 text-medium-emphasis">暂无做种数据</div>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'

const props = defineProps<{
  api?: { get: (path: string) => Promise<unknown>; post: (path: string, data?: unknown) => Promise<unknown> }
}>()

defineEmits<{ action: []; switch: []; close: [] }>()

const API = 'plugin/PTBonusCalc'

type TorrentRow = {
  name: string
  size: number
  seeders: number
  pubdate: string
  torrent_id?: string
  torrent_key?: string
  T_weeks: number
  A_value: number
  A_per_GB: number
  bonus_per_hour: number
  matched: boolean
  downloader_hash?: string
  downloader_ratio?: number
  downloader_uploaded?: number
  downloader_seeding_time?: number
  downloader_name?: string
  downloader_torrent_name?: string
  downloader_tracker?: string
  downloader_state?: string
  downloader_downloaded?: number
  downloader_size?: number
  downloader_added_at?: string | null
  suggested_match?: boolean
  detail_url?: string
}

type SiteData = {
  site_name: string
  domain: string
  site_url?: string
  total_bonus_per_hour: number
  total_seed_count?: number
  has_bonus_params: boolean
  bonus_params: Record<string, unknown>
  torrents: TorrentRow[]
}

const sites = ref<SiteData[]>([])
const loading = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const hoveredRow = ref<{ siteDomain: string; index: number } | null>(null)
const expandedDomains = ref<string[]>([])
const loadingDomain = ref<string | null>(null)
const savingDomain = ref<string | null>(null)
const dirtyDomains = ref<Set<string>>(new Set())
let isSyncingHScroll = false

/** 按站点存储的下载器候选（右表下拉），避免多站点同时展开时互相覆盖 */
type CandidateItem = { hash: string; name: string; total_size: number; ratio?: number; tracker?: string; downloader?: string; label: string }
const candidateDownloadersBySite = ref<Record<string, CandidateItem[]>>({})

/** 当前站点内未被任何行占用的候选 */
function getAvailableCandidatesForSite(site: SiteData): CandidateItem[] {
  const list = candidateDownloadersBySite.value[site.domain] || []
  const usedHashes = new Set((site.torrents || []).map(t => t.downloader_hash).filter(Boolean))
  return list.filter(c => !usedHashes.has(c.hash))
}

function isRowHovered(siteDomain: string, index: number): boolean {
  const h = hoveredRow.value
  return h !== null && h.siteDomain === siteDomain && h.index === index
}

function hasUnsavedChanges(domain: string): boolean {
  return dirtyDomains.value.has(domain)
}

async function refreshSite(domain: string) {
  const site = sites.value.find(s => s.domain === domain)
  if (!site || !props.api?.post) return
  const idx = sites.value.findIndex(s => s.domain === domain)
  if (idx !== -1) {
    sites.value[idx] = { ...sites.value[idx], torrents: [] }
  }
  loadingDomain.value = domain
  try {
    const res = (await props.api.post(`${API}/bonus_data`, { site_id: domain })) as {
      success?: boolean
      message?: string
      left_table?: TorrentRow[]
      right_table?: Array<{ hash: string; name: string; total_size: number; ratio?: number; tracker?: string; downloader?: string }>
    }
    if (res?.success === false) {
      errorMessage.value = res?.message || '加载失败'
    } else {
      if (idx !== -1) {
        sites.value[idx] = { ...sites.value[idx], torrents: Array.isArray(res?.left_table) ? res.left_table : [] }
      }
      const flat = Array.isArray(res?.right_table) ? res.right_table : []
      candidateDownloadersBySite.value = {
        ...candidateDownloadersBySite.value,
        [domain]: flat.map(t => ({
          hash: t.hash,
          name: t.name || '',
          total_size: t.total_size || 0,
          ratio: t.ratio,
          tracker: t.tracker,
          downloader: t.downloader,
          label: `${(t.name || '').slice(0, 50)}${(t.name || '').length > 50 ? '…' : ''} (${formatSize(t.total_size)})`,
        })),
      }
      dirtyDomains.value.delete(domain)
    }
  } catch (e) {
    console.error('refreshSite', e)
    errorMessage.value = (e as Error)?.message || '加载失败'
  } finally {
    loadingDomain.value = null
  }
}

async function saveSiteData(domain: string) {
  if (!props.api?.post) return
  const site = sites.value.find(s => s.domain === domain)
  if (!site) return
  savingDomain.value = domain
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const associations: Array<{ site_domain: string; torrent_key: string; downloader_hash?: string }> = []
    for (const r of site.torrents) {
      const torrent_key = r.torrent_key || (r.torrent_id != null ? String(r.torrent_id) : '') || `${r.name}|${r.size}`
      if (!torrent_key) continue
      associations.push({
        site_domain: domain,
        torrent_key,
        downloader_hash: r.downloader_hash || undefined,
      })
    }
    const res = (await props.api.post(`${API}/save_data`, { associations })) as { success?: boolean; message?: string }
    if (res?.success) {
      successMessage.value = res?.message || '保存成功'
      dirtyDomains.value.delete(domain)
      await refreshSite(domain)
    } else {
      errorMessage.value = res?.message || '保存失败'
    }
  } catch (e) {
    console.error('saveSiteData', e)
    errorMessage.value = (e as Error)?.message || '保存失败'
  } finally {
    savingDomain.value = null
  }
}

function seedDetailUrl(site: SiteData, r: TorrentRow): string {
  if (r.detail_url) return r.detail_url
  const base = (site.site_url || '').replace(/\/+$/, '')
  if (!base) return '#'
  const id = r.torrent_id != null && r.torrent_id !== '' ? String(r.torrent_id) : (r.torrent_key || '')
  if (!id) return base
  return `${base}/details.php?id=${encodeURIComponent(id)}`
}

function onSelectMatch(siteDomain: string, rowIdx: number, hash: string) {
  const site = sites.value.find(s => s.domain === siteDomain)
  if (!site || rowIdx < 0 || rowIdx >= site.torrents.length) return
  const c = (candidateDownloadersBySite.value[siteDomain] || []).find(x => x.hash === hash)
  if (!c) return
  const r = site.torrents[rowIdx]
  r.downloader_hash = c.hash
  r.downloader_torrent_name = c.name
  r.downloader_name = c.downloader
  r.downloader_size = c.total_size
  r.downloader_ratio = c.ratio
  r.downloader_tracker = c.tracker
  r.downloader_state = 'uploading'
  r.downloader_uploaded = 0
  r.downloader_downloaded = 0
  r.downloader_added_at = new Date().toISOString().slice(0, 16).replace('T', ' ')
  r.matched = true
  dirtyDomains.value = new Set([...dirtyDomains.value, siteDomain])
}

/** 取消当前行的匹配，后续接真实数据时可在此处调用 save_data 提交空 hash */
function onCancelMatch(siteDomain: string, rowIdx: number) {
  const site = sites.value.find(s => s.domain === siteDomain)
  if (!site || rowIdx < 0 || rowIdx >= site.torrents.length) return
  const r = site.torrents[rowIdx]
  r.downloader_hash = undefined
  r.downloader_torrent_name = undefined
  r.downloader_name = undefined
  r.downloader_size = undefined
  r.downloader_ratio = undefined
  r.downloader_tracker = undefined
  r.downloader_state = undefined
  r.downloader_uploaded = undefined
  r.downloader_downloaded = undefined
  r.downloader_added_at = undefined
  r.matched = false
  dirtyDomains.value = new Set([...dirtyDomains.value, siteDomain])
}

async function fetch() {
  if (!props.api?.post) return
  loading.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const res = (await props.api.post(`${API}/site_list`, {})) as { success?: boolean; message?: string; sites?: SiteData[] }
    if (res?.success === false) {
      errorMessage.value = res?.message || '加载失败'
      sites.value = []
    } else {
      sites.value = (res?.sites || []).map(s => ({
        ...s,
        torrents: Array.isArray(s.torrents) ? s.torrents : [],
      }))
      expandedDomains.value = []
    }
  } catch (e) {
    console.error('fetch site_list', e)
    errorMessage.value = (e as Error)?.message || '加载失败'
    sites.value = []
  } finally {
    loading.value = false
  }
}

async function fetchSiteData(domain: string) {
  if (!props.api?.post) return
  const site = sites.value.find(s => s.domain === domain)
  if (!site || site.torrents.length > 0) return
  loadingDomain.value = domain
  try {
    const res = (await props.api.post(`${API}/bonus_data`, { site_id: domain })) as {
      success?: boolean
      message?: string
      left_table?: TorrentRow[]
      right_table?: Array<{ hash: string; name: string; total_size: number; ratio?: number; tracker?: string; downloader?: string }>
    }
    if (res?.success === false) {
      errorMessage.value = res?.message || '加载失败'
    } else {
      const idx = sites.value.findIndex(s => s.domain === domain)
      if (idx !== -1) {
        sites.value[idx] = { ...sites.value[idx], torrents: Array.isArray(res?.left_table) ? res.left_table : [] }
      }
      const flat = Array.isArray(res?.right_table) ? res.right_table : []
      candidateDownloadersBySite.value = {
        ...candidateDownloadersBySite.value,
        [domain]: flat.map(t => ({
          hash: t.hash,
          name: t.name || '',
          total_size: t.total_size || 0,
          ratio: t.ratio,
          tracker: t.tracker,
          downloader: t.downloader,
          label: `${(t.name || '').slice(0, 50)}${(t.name || '').length > 50 ? '…' : ''} (${formatSize(t.total_size)})`,
        })),
      }
    }
  } catch (e) {
    console.error('fetch bonus_data site', e)
    errorMessage.value = (e as Error)?.message || '加载失败'
  } finally {
    loadingDomain.value = null
  }
}

watch(expandedDomains, (cur, prev) => {
  const added = (cur || []).filter(d => !(prev || []).includes(d))
  for (const domain of added) {
    const site = sites.value.find(s => s.domain === domain)
    if (site && site.torrents.length === 0 && loadingDomain.value !== domain) {
      fetchSiteData(domain)
    }
  }
})

function formatSize(bytes: number | undefined | null): string {
  if (bytes == null || bytes === 0) return '—'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return i === 0 ? `${v} ${u[i]}` : `${v.toFixed(2)} ${u[i]}`
}

function formatRatio(r: number | undefined | null): string {
  if (r == null) return '—'
  return Number(r).toFixed(2)
}

function formatBonus(v: number | undefined | null): string {
  if (v == null) return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}

function formatAddedAt(v: string | undefined | null): string {
  if (v == null || v === '') return '—'
  const s = String(v).trim()
  if (!s) return '—'
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

function onBodyColScroll(e: Event, side: 'left' | 'right') {
  if (isSyncingHScroll) return
  const col = e.currentTarget as HTMLElement
  const wrapper = col.closest('.site-tables-wrapper')
  if (!wrapper) return
  const header = wrapper.querySelector(`.tables-header-row .table-panel-${side} .table-header-fixed`) as HTMLElement
  if (header) {
    isSyncingHScroll = true
    header.scrollLeft = col.scrollLeft
    requestAnimationFrame(() => { isSyncingHScroll = false })
  }
}

function onHeaderScroll(e: Event, side: 'left' | 'right') {
  if (isSyncingHScroll) return
  const header = e.currentTarget as HTMLElement
  const wrapper = header.closest('.site-tables-wrapper')
  if (!wrapper) return
  const col = wrapper.querySelector(`.table-body-col.table-panel-${side}`) as HTMLElement
  if (col) {
    isSyncingHScroll = true
    col.scrollLeft = header.scrollLeft
    requestAnimationFrame(() => { isSyncingHScroll = false })
  }
}

onMounted(() => fetch())
</script>

<style scoped>
.page {
  padding: 16px;
  overflow: visible;
  --pt-border: rgba(0, 0, 0, 0.12);
  --pt-row-border: rgba(0, 0, 0, 0.08);
}
@media (prefers-color-scheme: dark) {
  .page {
    --pt-border: rgba(255, 255, 255, 0.12);
    --pt-row-border: rgba(255, 255, 255, 0.08);
  }
}
.site-tables-wrapper {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-height: 60vh;
  border: 1px solid var(--pt-border);
  border-radius: 6px;
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
}
.tables-header-row {
  display: flex;
  flex-direction: row;
  flex-shrink: 0;
  border-bottom: 1px solid var(--pt-row-border);
  background: rgb(var(--v-theme-surface));
}
.tables-header-row .table-panel {
  display: flex;
  min-width: 0;
  background: rgb(var(--v-theme-surface));
}
.tables-header-row .table-panel-left {
  flex: 0 0 34%;
  max-width: 400px;
  min-width: 260px;
  border-right: 1px solid var(--pt-border);
}
.tables-header-row .table-panel-right {
  flex: 1;
  min-width: 0;
}
.table-header-fixed {
  overflow-x: auto;
  overflow-y: hidden;
  background: rgb(var(--v-theme-surface));
  z-index: 2;
}
.table-header-fixed .seed-table {
  width: 100%;
  min-width: max-content;
  table-layout: fixed;
}
.tables-body-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  -webkit-overflow-scrolling: touch;
}
.tables-body-inner {
  display: flex;
  flex-direction: row;
  min-height: min-content;
}
.table-body-col {
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  background: rgb(var(--v-theme-surface));
}
.table-body-col.table-panel-left {
  flex: 0 0 34%;
  max-width: 400px;
  min-width: 260px;
  border-right: 1px solid var(--pt-border);
}
.table-body-col.table-panel-right {
  flex: 1;
  min-width: 0;
}
.table-body-col .seed-table {
  width: 100%;
  min-width: max-content;
  table-layout: fixed;
}
.table-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: rgb(var(--v-theme-surface));
}
.table-panel-left {
  flex: 0 0 34%;
  max-width: 400px;
  min-width: 260px;
  border-right: 1px solid var(--pt-border);
}
.table-panel-right {
  flex: 1;
  min-width: 0;
}
.seed-table {
  border-collapse: collapse;
}
.seed-table thead th {
  padding: 10px 12px;
  text-align: inherit;
  font-weight: 600;
  background: rgb(var(--v-theme-surface));
  border-bottom: 1px solid var(--pt-row-border);
  white-space: nowrap;
}
.seed-table tbody tr {
  height: 40px;
}
.seed-table tbody tr:nth-child(even) {
  background: rgba(0, 0, 0, 0.02);
}
@media (prefers-color-scheme: dark) {
  .seed-table tbody tr:nth-child(even) {
    background: rgba(255, 255, 255, 0.04);
  }
}
.seed-table tbody tr:hover {
  background: rgba(var(--v-theme-primary), 0.06);
}
.seed-table tbody tr.row-associated-hover {
  background: rgba(var(--v-theme-primary), 0.12);
}
.seed-table tbody tr.row-unmatched .col-name {
  background: rgba(var(--v-theme-warning), 0.08);
}
.cell-match {
  white-space: nowrap;
}
.cell-name-with-action {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.suggested-tag {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--v-warning-base);
  background: rgba(var(--v-theme-warning), 0.15);
  padding: 1px 4px;
  border-radius: 3px;
}
.cell-name-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cell-cancel-btn {
  flex-shrink: 0;
  min-width: 0;
  padding: 0 4px;
}
.cell-match .v-btn {
  min-width: 0;
  padding: 0 4px;
}
.seed-table tbody td {
  height: 40px;
  padding: 0 12px;
  vertical-align: middle;
  border-bottom: 1px solid var(--pt-row-border);
}
/* 除名称列外全部不换行，从根上保证左右表每行高度一致 */
.seed-table tbody td:not(.col-name) {
  white-space: nowrap;
}
.seed-table .col-num { width: 3.5rem; min-width: 3.5rem; }
.seed-table .col-name { width: 28%; min-width: 6rem; }
/* 左表：# 种子名 大小 做种 魔力/h */
.table-panel-left .seed-table .col-num:nth-of-type(1) { width: 3rem; min-width: 3rem; }
.table-panel-left .seed-table .col-name { width: 14rem; min-width: 14rem; }
.table-panel-left .seed-table .col-size { width: 7rem; min-width: 7rem; }
.table-panel-left .seed-table .col-num:nth-of-type(4) { width: 4rem; min-width: 4rem; }
.table-panel-left .seed-table .col-num:nth-of-type(5) { width: 5.5rem; min-width: 5.5rem; }
/* 右表：除名称外每列给足宽，避免撑出换行 */
.table-panel-right .seed-table .col-name { width: 28%; }
.table-panel-right .seed-table .col-num:nth-of-type(1) { width: 3rem; min-width: 3rem; }
.table-panel-right .seed-table .col-size:nth-of-type(3) { width: 7rem; min-width: 7rem; }
.table-panel-right .seed-table .col-tracker { width: 7rem; min-width: 6rem; }
.table-panel-right .seed-table .col-hash { width: 5rem; min-width: 4.5rem; }
.table-panel-right .seed-table .col-state { width: 4.5rem; min-width: 4rem; }
.table-panel-right .seed-table .col-size:nth-of-type(7),
.table-panel-right .seed-table .col-size:nth-of-type(8) { width: 6rem; min-width: 5.5rem; }
.table-panel-right .seed-table .col-num:nth-of-type(9) { width: 4.5rem; min-width: 4rem; }
.table-panel-right .seed-table .col-time { width: 10rem; min-width: 9rem; }
.seed-table .col-size { width: 7rem; min-width: 6rem; }
.seed-table .col-tracker { width: 7rem; min-width: 6rem; }
.seed-table .col-hash { width: 5rem; min-width: 4.5rem; }
.seed-table .col-state { width: 4.5rem; min-width: 4rem; }
.seed-table .col-time { width: 10rem; min-width: 9rem; }
.seed-table td.col-name,
.seed-table th.col-name {
  max-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.seed-name-link {
  color: rgb(var(--v-theme-primary));
  text-decoration: none;
  cursor: pointer;
  pointer-events: auto;
}
.seed-name-link:hover {
  text-decoration: underline;
}
.col-tracker {
  overflow: hidden;
  text-overflow: ellipsis;
}
.col-hash {
  font-size: 0.7rem;
  overflow: hidden;
  text-overflow: ellipsis;
}
@media (max-width: 768px) {
  .site-tables-wrapper {
    flex-direction: column;
  }
  .table-panel {
    max-height: 45vh;
  }
  .table-panel-left {
    flex: none;
    max-width: none;
    min-width: 0;
    border-right: none;
    border-bottom: 1px solid var(--pt-border);
  }
}
.font-mono {
  font-family: ui-monospace, monospace;
}
</style>
