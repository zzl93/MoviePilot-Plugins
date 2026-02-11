<template>
  <div class="config-page">
    <v-card>
      <v-card-title>PT魔力计算器 - 配置</v-card-title>
      <v-card-text>
        <v-select
          v-model="form.selected_sites"
          :items="formOptions.sites"
          item-title="title"
          item-value="value"
          label="显示站点"
          multiple
          chips
          clearable
          hint="选择要显示的站点，留空则显示所有启用站点"
          persistent-hint
          class="mb-4"
        />
        <v-select
          v-model="form.sync_downloaders"
          :items="formOptions.downloaders"
          item-title="title"
          item-value="value"
          label="同步数据下载器"
          multiple
          chips
          clearable
          hint="从这些下载器拉取种子数据用于关联，留空则不关联下载器"
          persistent-hint
          class="mb-4"
        />
        <v-btn color="primary" variant="tonal" @click="dialogOpen = true" class="mr-2">
          打开站点地址映射与种子关联管理
        </v-btn>
        <v-btn color="primary" @click="save" :loading="saving">保存配置</v-btn>
      </v-card-text>
    </v-card>

    <v-dialog v-model="dialogOpen" max-width="65rem" scrollable persistent>
      <v-card>
        <v-card-title>站点地址映射与种子关联管理</v-card-title>
        <v-card-text class="pt-2">
          <div class="text-h6 mb-2">站点地址映射</div>
          <div class="text-body-2 mb-4 text-grey">
            配置站点域名与下载器中的地址关键词映射。下拉选项来自同步数据下载器中种子的 tracker 域名。
          </div>
          <template v-for="site in sitesForMapping" :key="site.value">
            <div class="mb-3">
              <div class="text-body-2 mb-1">{{ site.title }}</div>
              <v-select
                :model-value="getSiteMapping(site.value)"
                @update:model-value="v => setSiteMapping(site.value, Array.isArray(v) ? v : [])"
                :items="formOptions.address_keyword_options"
                item-title="title"
                item-value="value"
                label="下载器地址关键词"
                multiple
                chips
                density="compact"
              />
            </div>
          </template>

          <v-divider class="my-4" />
          <div class="text-h6 mb-2">种子关联管理</div>
          <div class="text-body-2 mb-4 text-grey">
            当前有 {{ associationData.total_unmatched_count }} 个未匹配的站点种子。
            修改「显示站点」「同步数据下载器」或「站点地址映射」后点击刷新，再为每个种子选择对应的下载器种子。
          </div>
          <v-btn color="secondary" size="small" @click="refreshAssociation" :loading="associationLoading" class="mb-3">
            刷新候选
          </v-btn>
          <v-expansion-panels multiple>
            <v-expansion-panel
              v-for="(siteData, domain) in associationData.unmatched_by_site"
              :key="domain"
              class="mb-2"
            >
              <v-expansion-panel-title class="bg-blue-lighten-5">
                {{ siteData.site_name }}（共 {{ siteData.total_count }} 个做种，已匹配 {{ siteData.matched_count }} 个，{{ siteData.torrents.length }} 个未匹配）
              </v-expansion-panel-title>
              <v-expansion-panel-text>
                <div class="association-list">
                  <div
                    v-for="u in siteData.torrents"
                    :key="u.torrent_key"
                    class="d-flex align-center gap-4 mb-3 flex-wrap"
                  >
                    <div class="flex-grow-1 min-w-0" style="min-width: 120px">
                      <div class="text-body-2 text-truncate" :title="u.name">{{ u.name || '—' }}</div>
                      <div class="text-caption text-grey">大小: {{ formatSize(u.size) }}</div>
                    </div>
                    <v-autocomplete
                      :model-value="getTorrentMapping(domain, u.torrent_key)"
                      @update:model-value="v => setTorrentMapping(domain, u.torrent_key, v)"
                      :items="u.options || []"
                      item-title="display"
                      item-value="value"
                      density="compact"
                      placeholder="选择下载器种子"
                      clearable
                      hide-no-data
                      class="flex-grow-1"
                      style="max-width: 400px"
                    />
                  </div>
                </div>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="dialogOpen = false">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'

const props = defineProps<{
  initialConfig?: Record<string, unknown>
  api?: { get: (path: string) => Promise<unknown>; post: (path: string, body?: unknown) => Promise<unknown> }
}>()

const emit = defineEmits<{
  save: [value: Record<string, unknown>]
  close: []
  switch: []
}>()

const API = 'plugin/PTBonusCalc'

const form = ref<{
  selected_sites: string[]
  sync_downloaders: string[]
  [key: string]: unknown
}>({
  selected_sites: [],
  sync_downloaders: [],
})

const formOptions = ref<{
  sites: { title: string; value: string }[]
  downloaders: { title: string; value: string }[]
  address_keyword_options: { title: string; value: string }[]
  sites_with_config_data: string[]
}>({
  sites: [],
  downloaders: [],
  address_keyword_options: [],
  sites_with_config_data: [],
})

const associationData = ref<{
  total_unmatched_count: number
  unmatched_by_site: Record<string, { site_name: string; total_count: number; matched_count: number; torrents: Array<{ torrent_key: string; name: string; size: number; options?: Array<{ display: string; value: string; title: string }> }> }>
}>({
  total_unmatched_count: 0,
  unmatched_by_site: {},
})

const dialogOpen = ref(false)
const saving = ref(false)
const associationLoading = ref(false)

const sitesForMapping = computed(() => {
  const selected = new Set((form.value.selected_sites as string[]) || [])
  const withData = new Set(formOptions.value.sites_with_config_data || [])
  return formOptions.value.sites.filter(s => selected.has(s.value) && withData.has(s.value))
})

function fieldKey(torrentKey: string) {
  return torrentKey.replace(/\|/g, '_').replace(/\//g, '_')
}

function getSiteMapping(domain: string): string[] {
  const v = form.value['site_address_mapping_' + domain]
  return Array.isArray(v) ? [...v] : []
}

function getTorrentMapping(domain: string, torrentKey: string): string | null {
  const v = form.value['torrent_mapping_' + domain + '_' + fieldKey(torrentKey)]
  return v != null && typeof v === 'string' ? v : null
}

function formatSize(bytes: number) {
  if (!bytes) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i]
}

function setSiteMapping(domain: string, val: string[] | readonly string[]) {
  form.value['site_address_mapping_' + domain] = Array.isArray(val) ? [...val] : []
}

function setTorrentMapping(siteDomain: string, torrentKey: string, val: unknown) {
  const key = 'torrent_mapping_' + siteDomain + '_' + fieldKey(torrentKey)
  if (val != null && typeof val === 'string') {
    form.value[key] = val
  } else {
    delete form.value[key]
  }
}

async function fetchFormOptions() {
  if (!props.api?.get) return
  try {
    const res = (await props.api.get(`${API}/form_options`)) as typeof formOptions.value
    formOptions.value = res || formOptions.value
  } catch (e) {
    console.error('fetchFormOptions', e)
  }
}

async function fetchAssociationData() {
  if (!props.api?.post) return
  associationLoading.value = true
  try {
    const payload = {
      selected_sites: form.value.selected_sites,
      sync_downloaders: form.value.sync_downloaders,
      site_address_mappings: {} as Record<string, string[]>,
    }
    for (const site of sitesForMapping.value) {
      const v = form.value[`site_address_mapping_${site.value}`]
      if (v && Array.isArray(v)) {
        payload.site_address_mappings[site.value] = v
      }
    }
    const res = (await props.api.post(`${API}/seed_association_data`, payload)) as typeof associationData.value
    associationData.value = res || associationData.value
  } catch (e) {
    console.error('fetchAssociationData', e)
  } finally {
    associationLoading.value = false
  }
}

function refreshAssociation() {
  fetchAssociationData()
}

function buildSaveConfig(): Record<string, unknown> {
  const cfg: Record<string, unknown> = {
    selected_sites: form.value.selected_sites,
    sync_downloaders: form.value.sync_downloaders,
  }
  for (const [k, v] of Object.entries(form.value)) {
    if (k.startsWith('site_address_mapping_') || k.startsWith('torrent_mapping_')) {
      if (v !== undefined && v !== null && v !== '') {
        cfg[k] = v
      }
    }
  }
  return cfg
}

function save() {
  emit('save', buildSaveConfig())
  saving.value = true
  setTimeout(() => { saving.value = false }, 500)
}

onMounted(async () => {
  const init = props.initialConfig || {}
  form.value = {
    selected_sites: Array.isArray(init.selected_sites) ? [...init.selected_sites] : [],
    sync_downloaders: Array.isArray(init.sync_downloaders) ? [...init.sync_downloaders] : [],
  }
  for (const [k, v] of Object.entries(init)) {
    if (k.startsWith('site_address_mapping_') || k.startsWith('torrent_mapping_')) {
      form.value[k] = v
    }
  }
  await fetchFormOptions()
  await fetchAssociationData()
})

watch(dialogOpen, val => {
  if (val) fetchAssociationData()
})
</script>

<style scoped>
.config-page {
  padding: 16px;
}
.association-list {
  max-height: 320px;
  overflow-y: auto;
}
</style>
