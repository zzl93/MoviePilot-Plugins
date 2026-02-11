<template>
  <div class="page">
    <div v-if="!sites.length && !loading" class="text-center pa-4 text-body-1">
      暂无做种数据或未配置站点。请先在插件配置中勾选「显示站点」并保存，再刷新站点用户数据。
    </div>
    <div v-else>
      <v-btn color="primary" variant="tonal" @click="fetch" :loading="loading" class="mb-3">刷新</v-btn>
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
            <v-table v-if="site.torrents.length" hover class="mt-2">
              <thead>
                <tr>
                  <th class="text-end">序号</th>
                  <th class="text-start" style="max-width: 220px">种子名</th>
                  <th class="text-end">大小</th>
                  <th class="text-end">做种人数</th>
                  <th class="text-start">发布时间</th>
                  <th class="text-end">T(周)</th>
                  <th class="text-end">A值</th>
                  <th class="text-end">A/GB</th>
                  <th class="text-end">每小时魔力</th>
                  <th class="text-end">关联状态</th>
                  <th class="text-end">分享率</th>
                  <th class="text-end">上传量</th>
                  <th class="text-end">做种时长</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, idx) in site.torrents" :key="idx" class="text-caption">
                  <td class="text-end">{{ idx + 1 }}</td>
                  <td class="text-start text-truncate" :title="r.name" style="max-width: 220px">{{ (r.name || '—').slice(0, 80) }}{{ (r.name || '').length > 80 ? '...' : '' }}</td>
                  <td class="text-end">{{ formatSize(r.size) }}</td>
                  <td class="text-end">{{ r.seeders }}</td>
                  <td class="text-start">{{ r.pubdate || '—' }}</td>
                  <td class="text-end">{{ r.T_weeks }}</td>
                  <td class="text-end">{{ r.A_value }}</td>
                  <td class="text-end">{{ r.A_per_GB }}</td>
                  <td class="text-end font-weight-medium">{{ r.bonus_per_hour }}</td>
                  <td class="text-end">{{ r.matched ? '✓ 已关联' : '✗ 未关联' }}</td>
                  <td class="text-end">{{ r.matched ? (r.downloader_ratio ?? 0).toFixed(2) : '—' }}</td>
                  <td class="text-end">{{ r.matched ? formatSize(r.downloader_uploaded ?? 0) : '—' }}</td>
                  <td class="text-end">{{ r.matched ? formatSeedingTime(r.downloader_seeding_time ?? 0) : '—' }}</td>
                </tr>
              </tbody>
            </v-table>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

const props = defineProps<{
  api?: { get: (path: string) => Promise<unknown> }
}>()

defineEmits<{ action: []; switch: []; close: [] }>()

const API = 'plugin/PTBonusCalc'

const sites = ref<Array<{
  site_name: string
  domain: string
  total_bonus_per_hour: number
  has_bonus_params: boolean
  bonus_params: Record<string, unknown>
  torrents: Array<{
    name: string
    size: number
    seeders: number
    pubdate: string
    T_weeks: number
    A_value: number
    A_per_GB: number
    bonus_per_hour: number
    matched: boolean
    downloader_ratio?: number
    downloader_uploaded?: number
    downloader_seeding_time?: number
  }>
}>>([])

const loading = ref(false)

function formatSize(bytes: number) {
  if (!bytes) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1)
  return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i]
}

function formatSeedingTime(seconds: number) {
  if (seconds <= 0) return '0秒'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}天${hours}小时`
  if (hours > 0) return `${hours}小时${minutes}分钟`
  return `${minutes}分钟`
}

function formatBonus(val: number, params?: Record<string, unknown>) {
  const decimals = Math.min(Math.max(0, parseInt(String(params?.hourly_bonus_decimals ?? 2), 10) || 2), 6)
  return val.toFixed(decimals)
}

async function fetch() {
  if (!props.api?.get) return
  loading.value = true
  try {
    const res = (await props.api.get(`${API}/bonus_seeding_list`)) as { sites?: typeof sites.value }
    sites.value = res?.sites || []
  } catch (e) {
    console.error('fetch bonus_seeding_list', e)
    sites.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => fetch())
</script>

<style scoped>
.page {
  padding: 16px;
}
</style>
