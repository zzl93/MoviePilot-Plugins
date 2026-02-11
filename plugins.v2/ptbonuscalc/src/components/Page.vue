<template>
  <div class="page">
    <div v-if="!sites.length && !loading" class="text-center pa-4 text-body-1">
      暂无做种数据或未配置站点。请先在插件配置中勾选「显示站点」并保存，再刷新站点用户数据。
    </div>
    <div v-else>
      <div class="d-flex ga-2 mb-3">
        <v-btn color="primary" variant="tonal" @click="fetch" :loading="loading">刷新</v-btn>
        <v-btn color="success" variant="tonal" @click="saveData" :loading="saving">保存数据</v-btn>
      </div>
      <v-expansion-panels>
        <v-expansion-panel v-for="site in sites" :key="site.domain">
          <v-expansion-panel-title class="bg-blue-lighten-5">
            {{ site.site_name }}（{{ site.torrents.length }} 个做种，时魔 {{ formatBonus(site.total_bonus_per_hour, site.bonus_params) }}）
          </v-expansion-panel-title>
          <v-expansion-panel-text>
            <div class="text-body-2 mb-2" v-if="site.torrents.length">
              共 {{ site.torrents.length }} 个做种，每小时总魔力：{{ formatBonus(site.total_bonus_per_hour, site.bonus_params) }}
              <span v-if="!site.has_bonus_params" class="text-orange">[未获取到站点魔力参数 T0/N0/B0/L，魔力为 0]</span>
            </div>
            <div v-else class="text-body-2">{{ site.site_name }}：无做种记录</div>
            <div v-if="site.torrents.length" class="site-tables-wrapper">
              <div class="table-scroll-left">
                <div class="table-left-inner">
                  <v-table hover density="compact" class="text-caption seed-table">
                    <thead class="table-header-sticky">
                      <tr>
                        <th class="text-end">#</th>
                        <th class="text-start">种子名</th>
                        <th class="text-end">大小</th>
                        <th class="text-end">做种</th>
                        <th class="text-end">魔力/h</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(r, idx) in sortedLeft(site)"
                        :key="'L-' + site.domain + '-' + (r.torrent_key || idx)"
                        class="seed-row"
                        @dragover.prevent="dragOver($event)"
                        @drop="dropLink(site.domain, r, $event)"
                      >
                        <td class="text-end">{{ idx + 1 }}</td>
                        <td class="text-start text-truncate" :title="r.name" style="max-width: 200px">{{ (r.name || '—').slice(0, 60) }}{{ (r.name || '').length > 60 ? '...' : '' }}</td>
                        <td class="text-end">{{ formatSize(r.size) }}</td>
                        <td class="text-end">{{ r.seeders }}</td>
                        <td class="text-end font-weight-medium">{{ r.bonus_per_hour }}</td>
                      </tr>
                    </tbody>
                  </v-table>
                </div>
                <div class="table-link-col">
                  <div class="link-col-header"></div>
                  <template v-for="(r, idx) in sortedLeft(site)" :key="'M-' + site.domain + '-' + (r.torrent_key || idx)">
                    <div
                      v-if="isLinked(site.domain, r)"
                      class="link-cell linked"
                      @click="unlink(site.domain, r)"
                    >
                      <v-icon size="24" color="success">mdi-link-variant</v-icon>
                    </div>
                    <v-menu v-else location="bottom" close-on-content-click>
                      <template #activator="{ props: menuProps }">
                        <div class="link-cell" v-bind="menuProps">
                          <v-icon size="24" color="grey">mdi-link-variant-off</v-icon>
                        </div>
                      </template>
                      <v-list density="compact">
                        <v-list-item
                          v-for="d in downloadersForSite(site.domain)"
                          :key="d.hash"
                          @click="linkTo(site.domain, r, d.hash)"
                        >
                          <v-list-item-title class="text-caption">{{ (d.name || '—').slice(0, 50) }}{{ (d.name || '').length > 50 ? '...' : '' }} ({{ d.tracker || '—' }})</v-list-item-title>
                        </v-list-item>
                        <v-list-item v-if="!downloadersForSite(site.domain).length" disabled>
                          <v-list-item-title class="text-caption">无匹配的下载器种子，请先在设置中配置站点地址映射</v-list-item-title>
                        </v-list-item>
                      </v-list>
                    </v-menu>
                  </template>
                </div>
              </div>
              <div class="table-scroll-right">
                <v-table hover density="compact" class="text-caption seed-table">
                  <thead class="table-header-sticky">
                    <tr>
                      <th class="text-end">#</th>
                      <th class="text-start">Hash</th>
                      <th class="text-start">地址</th>
                      <th class="text-start">种子名</th>
                      <th class="text-end">大小</th>
                      <th class="text-end">分享率</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="(d, idx) in sortedRight(site)"
                      :key="'R-' + d.hash"
                      draggable="true"
                      @dragstart="dragStart($event, d)"
                      class="seed-row draggable-row"
                    >
                      <td class="text-end">{{ idx + 1 }}</td>
                      <td class="text-start font-mono text-caption" style="font-size: 0.7rem">{{ d.hash }}</td>
                      <td class="text-start text-truncate" :title="d.tracker" style="max-width: 120px">{{ (d.tracker || '—').slice(0, 25) }}{{ (d.tracker || '').length > 25 ? '...' : '' }}</td>
                      <td class="text-start text-truncate" :title="d.name" style="max-width: 150px">{{ (d.name || '—').slice(0, 40) }}{{ (d.name || '').length > 40 ? '...' : '' }}</td>
                      <td class="text-end">{{ formatSize(d.total_size || 0) }}</td>
                      <td class="text-end">{{ (d.ratio ?? 0).toFixed(2) }}</td>
                    </tr>
                  </tbody>
                </v-table>
              </div>
            </div>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

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
}

type DownloaderTorrent = { hash: string; name: string; total_size?: number; ratio?: number; tracker?: string }

type SiteData = {
  site_name: string
  domain: string
  total_bonus_per_hour: number
  has_bonus_params: boolean
  bonus_params: Record<string, unknown>
  torrents: TorrentRow[]
}

const sites = ref<SiteData[]>([])
const downloaderTorrentsBySite = ref<Record<string, DownloaderTorrent[]>>({})
const loading = ref(false)
const saving = ref(false)
const localLinks = ref<Record<string, string>>({})
let dragItem: DownloaderTorrent | null = null

function getKey(domain: string, r: TorrentRow) {
  return `${domain}|${r.torrent_key || r.torrent_id || `${r.name}|${r.size}`}`
}

function isLinked(domain: string, r: TorrentRow): boolean {
  const k = getKey(domain, r)
  const h = localLinks.value[k] ?? (r.matched ? r.downloader_hash : undefined)
  return !!h
}

function getLinkedHash(domain: string, r: TorrentRow): string | undefined {
  const k = getKey(domain, r)
  return localLinks.value[k] ?? (r.matched ? r.downloader_hash : undefined)
}

function unlink(domain: string, r: TorrentRow) {
  const k = getKey(domain, r)
  const next = { ...localLinks.value }
  delete next[k]
  localLinks.value = next
}

function linkTo(domain: string, r: TorrentRow, hash: string) {
  const k = getKey(domain, r)
  localLinks.value = { ...localLinks.value, [k]: hash }
}

function downloadersForSite(domain: string): DownloaderTorrent[] {
  return downloaderTorrentsBySite.value[domain] || []
}

function dragStart(ev: DragEvent, d: DownloaderTorrent) {
  dragItem = d
  ev.dataTransfer?.setData('text/plain', d.hash)
  ev.dataTransfer!.effectAllowed = 'link'
}

function dragOver(ev: DragEvent) {
  ev.preventDefault()
  ev.dataTransfer!.dropEffect = 'link'
}

function dropLink(domain: string, r: TorrentRow, ev: DragEvent) {
  ev.preventDefault()
  if (!dragItem) return
  const k = getKey(domain, r)
  localLinks.value = { ...localLinks.value, [k]: dragItem.hash }
  dragItem = null
}

function sortedLeft(site: SiteData): TorrentRow[] {
  const arr = [...site.torrents]
  const linked = (r: TorrentRow) => isLinked(site.domain, r)
  const tid = (r: TorrentRow) => r.torrent_id || r.torrent_key || ''
  arr.sort((a, b) => {
    const al = linked(a)
    const bl = linked(b)
    if (al !== bl) return al ? 1 : -1
    return String(tid(a)).localeCompare(String(tid(b)))
  })
  return arr
}

function sortedRight(site: SiteData): DownloaderTorrent[] {
  const list = downloadersForSite(site.domain)
  const linkedSet = new Set<string>()
  for (const r of site.torrents) {
    const h = getLinkedHash(site.domain, r)
    if (h) linkedSet.add(h)
  }
  const linked: DownloaderTorrent[] = []
  const unlinked: DownloaderTorrent[] = []
  for (const d of list) {
    if (linkedSet.has(d.hash)) linked.push(d)
    else unlinked.push(d)
  }
  linked.sort((a, b) => (a.hash || '').localeCompare(b.hash || ''))
  unlinked.sort((a, b) => (a.hash || '').localeCompare(b.hash || ''))
  return [...unlinked, ...linked]
}

function formatSize(bytes: number) {
  if (!bytes) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1)
  return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i]
}

function formatBonus(val: number, params?: Record<string, unknown>) {
  const decimals = Math.min(Math.max(0, parseInt(String(params?.hourly_bonus_decimals ?? 2), 10) || 2), 6)
  return val.toFixed(decimals)
}

async function fetch() {
  if (!props.api?.post) return
  loading.value = true
  try {
    const res = (await props.api.post(`${API}/bonus_data`, {})) as {
      success?: boolean
      sites?: SiteData[]
      downloader_torrents_by_site?: Record<string, DownloaderTorrent[]>
    }
    sites.value = res?.sites || []
    downloaderTorrentsBySite.value = res?.downloader_torrents_by_site || {}
    const next: Record<string, string> = {}
    for (const site of sites.value) {
      for (const r of site.torrents) {
        if (r.matched && r.downloader_hash) {
          next[getKey(site.domain, r)] = r.downloader_hash
        }
      }
    }
    localLinks.value = next
  } catch (e) {
    console.error('fetch bonus_data', e)
    sites.value = []
    downloaderTorrentsBySite.value = {}
  } finally {
    loading.value = false
  }
}

function buildAssociations() {
  const list: Array<{ site_domain: string; torrent_key: string; downloader_hash?: string }> = []
  for (const site of sites.value) {
    for (const r of site.torrents) {
      const h = getLinkedHash(site.domain, r)
      const wasLinked = r.matched && r.downloader_hash
      const tkey = r.torrent_key || r.torrent_id || `${r.name}|${r.size}`
      if (h) {
        list.push({ site_domain: site.domain, torrent_key: tkey, downloader_hash: h })
      } else if (wasLinked) {
        list.push({ site_domain: site.domain, torrent_key: tkey })
      }
    }
  }
  return list
}

async function saveData() {
  if (!props.api?.post) return
  saving.value = true
  try {
    const associations = buildAssociations()
    const res = (await props.api.post(`${API}/save_data`, { associations })) as { success?: boolean; message?: string }
    if (res?.success) {
      await fetch()
    } else {
      console.error('save_data', res?.message)
    }
  } catch (e) {
    console.error('save_data', e)
  } finally {
    saving.value = false
  }
}

onMounted(() => fetch())
</script>

<style scoped>
.page {
  padding: 16px;
  overflow: visible;
}
.site-tables-wrapper {
  margin-top: 8px;
  display: flex;
  gap: 0;
  width: 100%;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 4px;
  overflow: hidden;
}
.table-scroll-left {
  flex: 1;
  min-width: 0;
  max-height: 60vh;
  overflow: auto;
  display: flex;
  gap: 0;
  border-right: 1px solid rgba(0, 0, 0, 0.12);
}
.table-scroll-right {
  flex: 1;
  min-width: 0;
  max-height: 60vh;
  overflow: auto;
}
.table-left-inner {
  flex-shrink: 0;
}
.seed-table :deep(.table-header-sticky) {
  position: sticky;
  top: 0;
  z-index: 2;
  background: rgb(var(--v-theme-surface));
}
.seed-table :deep(.table-header-sticky th) {
  background: rgb(var(--v-theme-surface));
  box-shadow: inset 0 -1px 0 rgba(0, 0, 0, 0.12);
}
.table-link-col {
  width: 56px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
}
.link-col-header {
  height: 40px;
  min-height: 40px;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  z-index: 2;
  background: rgb(var(--v-theme-surface));
  box-shadow: inset 0 -1px 0 rgba(0, 0, 0, 0.12);
  width: 100%;
}
.link-cell {
  min-height: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.2s;
}
.link-cell:hover {
  background: rgba(0, 0, 0, 0.04);
}
.link-cell.linked {
  background: rgba(76, 175, 80, 0.08);
}
.seed-table :deep(tbody tr) {
  height: 40px;
}
.seed-table :deep(tbody td) {
  height: 40px;
  padding: 0 12px;
  vertical-align: middle;
}
.draggable-row {
  cursor: grab;
}
.draggable-row:active {
  cursor: grabbing;
}
.font-mono {
  font-family: ui-monospace, monospace;
}
</style>
