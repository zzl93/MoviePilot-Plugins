<template>
  <v-card>
    <v-card-title>PT魔力计算器</v-card-title>
    <v-card-text>
      <div class="d-flex flex-wrap gap-4">
        <div>
          <span class="text-caption text-grey">站点数</span>
          <div class="text-h6">{{ sites.length }}</div>
        </div>
        <div>
          <span class="text-caption text-grey">总做种数</span>
          <div class="text-h6">{{ totalSeeding }}</div>
        </div>
        <div>
          <span class="text-caption text-grey">总时魔</span>
          <div class="text-h6">{{ totalBonus }}</div>
        </div>
        <div>
          <span class="text-caption text-grey">未匹配</span>
          <div class="text-h6">{{ unmatchedCount }}</div>
        </div>
      </div>
    </v-card-text>
  </v-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'

const props = defineProps<{
  config?: Record<string, unknown>
  allowRefresh?: boolean
  api?: { get: (path: string) => Promise<unknown>; post: (path: string, body?: unknown) => Promise<unknown> }
}>()

const API = 'plugin/PTBonusCalc'

const sites = ref<Array<{ torrents: unknown[]; total_bonus_per_hour: number; bonus_params?: Record<string, unknown> }>>([])
const unmatchedCount = ref(0)

const totalSeeding = computed(() => sites.value.reduce((s, site) => s + (site.torrents?.length || 0), 0))

const totalBonus = computed(() => {
  let sum = 0
  for (const site of sites.value) {
    sum += site.total_bonus_per_hour || 0
  }
  const decimals = 2
  return sum.toFixed(decimals)
})

onMounted(async () => {
  if (!props.api?.get) return
  try {
    const res = (await props.api.get(`${API}/bonus_seeding_list`)) as { sites?: typeof sites.value }
    sites.value = res?.sites || []
  } catch {
    sites.value = []
  }
  try {
    const assoc = (await props.api.post?.(`${API}/seed_association_data`, {})) as { total_unmatched_count?: number }
    unmatchedCount.value = assoc?.total_unmatched_count ?? 0
  } catch {
    unmatchedCount.value = 0
  }
})
</script>
