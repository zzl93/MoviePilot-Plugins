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
            {{ site.site_name }}（{{ site.torrents.length }} 个做种，时魔 {{ site.total_bonus_per_hour }}）
          </v-expansion-panel-title>
          <v-expansion-panel-text>
            <div class="text-body-2 mb-2" v-if="site.torrents.length">
              共 {{ site.torrents.length }} 个做种，每小时总魔力：{{ site.total_bonus_per_hour }}
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
                        v-for="(r, idx) in site.torrents"
                        :key="'L-' + site.domain + '-' + (r.torrent_key || idx)"
                        class="seed-row"
                      >
                        <td class="text-end">{{ idx + 1 }}</td>
                        <td class="text-start">{{ r.name }}</td>
                        <td class="text-end">{{ r.size }}</td>
                        <td class="text-end">{{ r.seeders }}</td>
                        <td class="text-end">{{ r.bonus_per_hour }}</td>
                      </tr>
                    </tbody>
                  </v-table>
                </div>
              </div>

              <div class="table-scroll-right">
                <v-table hover density="compact" class="text-caption seed-table">
                  <thead class="table-header-sticky">
                    <tr>
                      <th class="text-end">#</th>
                      <th class="text-start">名称</th>
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
                  <tbody>
                    <tr
                      v-for="(r, idx) in site.torrents"
                      :key="'R-' + site.domain + '-' + (r.torrent_key || idx)"
                      class="seed-row"
                    >
                      <td class="text-end">{{ idx + 1 }}</td>
                      <td class="text-start">{{ r.downloader_torrent_name || r.downloader_name || '—' }}</td>
                      <td class="text-end">{{ r.downloader_size }}</td>
                      <td class="text-start">{{ r.downloader_tracker }}</td>
                      <td class="text-start font-mono text-caption" style="font-size: 0.7rem">{{ r.downloader_hash }}</td>
                      <td class="text-start">{{ r.downloader_state }}</td>
                      <td class="text-end">{{ r.downloader_uploaded }}</td>
                      <td class="text-end">{{ r.downloader_downloaded }}</td>
                      <td class="text-end">{{ r.downloader_ratio }}</td>
                      <td class="text-start text-caption">{{ r.downloader_added_at }}</td>
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
  downloader_name?: string
  downloader_torrent_name?: string
  downloader_tracker?: string
  downloader_state?: string
  downloader_downloaded?: number
  downloader_size?: number
  downloader_added_at?: string | null
}

type SiteData = {
  site_name: string
  domain: string
  total_bonus_per_hour: number
  has_bonus_params: boolean
  bonus_params: Record<string, unknown>
  torrents: TorrentRow[]
}

const sites = ref<SiteData[]>([])
const loading = ref(false)
const saving = ref(false)

async function fetch() {
  if (!props.api?.post) return
  loading.value = true
  try {
    const res = (await props.api.post(`${API}/bonus_data`, {})) as { success?: boolean; sites?: SiteData[] }
    sites.value = res?.sites || []
  } catch (e) {
    console.error('fetch bonus_data', e)
    sites.value = []
  } finally {
    loading.value = false
  }
}

async function saveData() {
  if (!props.api?.post) return
  saving.value = true
  try {
    const associations: Array<{ site_domain: string; torrent_key: string; downloader_hash?: string }> = []
    for (const site of sites.value) {
      for (const r of site.torrents) {
        if (r.downloader_hash) {
          associations.push({
            site_domain: site.domain,
            torrent_key: r.torrent_key || r.torrent_id || `${r.name}|${r.size}`,
            downloader_hash: r.downloader_hash,
          })
        }
      }
    }
    const res = (await props.api.post(`${API}/save_data`, { associations })) as { success?: boolean; message?: string }
    if (res?.success) await fetch()
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
.table-scroll-left {
  border-right: none;
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
.seed-table :deep(tbody tr) {
  height: 40px;
}
.seed-table :deep(tbody td) {
  height: 40px;
  padding: 0 12px;
  vertical-align: middle;
}
.font-mono {
  font-family: ui-monospace, monospace;
}
</style>
