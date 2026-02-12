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
          v-model="form.primary_downloaders"
          :items="primaryDownloaderItems"
          item-title="title"
          item-value="value"
          label="主下载器"
          multiple
          chips
          clearable
          hint="主种下载器，与辅种下载器互斥"
          persistent-hint
          class="mb-4"
        />
        <v-select
          v-model="form.aux_downloaders"
          :items="auxDownloaderItems"
          item-title="title"
          item-value="value"
          label="辅种下载器"
          multiple
          chips
          clearable
          hint="辅种下载器，与主下载器互斥"
          persistent-hint
          class="mb-4"
        />
        <div class="mb-4">
          <div class="text-h6 mb-2">站点地址映射</div>
          <div class="text-body-2 mb-3 text-grey">
            配置站点域名与下载器中的地址关键词映射。下拉选项来自主/辅下载器中种子的 tracker 域名。请先在「显示站点」中选择要配置的站点。
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
        </div>
        <v-btn color="primary" @click="save" :loading="saving">保存配置</v-btn>
      </v-card-text>
    </v-card>
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
  primary_downloaders: string[]
  aux_downloaders: string[]
  [key: string]: unknown
}>({
  selected_sites: [],
  primary_downloaders: [],
  aux_downloaders: [],
})

const formOptions = ref<{
  sites: { title: string; value: string }[]
  downloaders: { title: string; value: string }[]
  address_keyword_options: { title: string; value: string }[]
  sites_with_config_data: string[]
  suggested_site_mappings: Record<string, string[]>
}>({
  sites: [],
  downloaders: [],
  address_keyword_options: [],
  sites_with_config_data: [],
  suggested_site_mappings: {},
})

const saving = ref(false)

const sitesForMapping = computed(() => {
  const selected = new Set((form.value.selected_sites as string[]) || [])
  return formOptions.value.sites.filter(s => selected.has(s.value))
})

const primaryDownloaderItems = computed(() => {
  const aux = new Set((form.value.aux_downloaders as string[]) || [])
  return formOptions.value.downloaders.filter(d => !aux.has(d.value))
})

const auxDownloaderItems = computed(() => {
  const primary = new Set((form.value.primary_downloaders as string[]) || [])
  return formOptions.value.downloaders.filter(d => !primary.has(d.value))
})

watch(
  () => form.value.primary_downloaders,
  (primary) => {
    const aux = form.value.aux_downloaders as string[]
    const overlap = aux?.filter(d => primary?.includes(d)) || []
    if (overlap.length) {
      form.value.aux_downloaders = aux.filter(d => !primary?.includes(d))
    }
  },
  { deep: true }
)
watch(
  () => form.value.aux_downloaders,
  (aux) => {
    const primary = form.value.primary_downloaders as string[]
    const overlap = primary?.filter(d => aux?.includes(d)) || []
    if (overlap.length) {
      form.value.primary_downloaders = primary.filter(d => !aux?.includes(d))
    }
  },
  { deep: true }
)

function getSiteMapping(domain: string): string[] {
  const v = form.value['site_address_mapping_' + domain]
  if (Array.isArray(v) && v.length) return [...v]
  const suggested = formOptions.value.suggested_site_mappings?.[domain]
  return Array.isArray(suggested) ? [...suggested] : []
}

function setSiteMapping(domain: string, val: string[] | readonly string[]) {
  form.value['site_address_mapping_' + domain] = Array.isArray(val) ? [...val] : []
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

function buildSaveConfig(): Record<string, unknown> {
  const cfg: Record<string, unknown> = {
    selected_sites: form.value.selected_sites,
    primary_downloaders: form.value.primary_downloaders,
    aux_downloaders: form.value.aux_downloaders,
  }
  for (const [k, v] of Object.entries(form.value)) {
    if (k.startsWith('site_address_mapping_')) {
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
  const legacySync = Array.isArray(init.sync_downloaders) ? init.sync_downloaders as string[] : []
  form.value = {
    selected_sites: Array.isArray(init.selected_sites) ? [...init.selected_sites] : [],
    primary_downloaders: Array.isArray(init.primary_downloaders) ? [...init.primary_downloaders] : legacySync,
    aux_downloaders: Array.isArray(init.aux_downloaders) ? [...init.aux_downloaders] : [],
  }
  for (const [k, v] of Object.entries(init)) {
    if (k.startsWith('site_address_mapping_')) {
      form.value[k] = v
    }
  }
  await fetchFormOptions()
  const suggested = formOptions.value.suggested_site_mappings || {}
  for (const site of formOptions.value.sites) {
    const domain = site.value
    if (!domain || form.value['site_address_mapping_' + domain] !== undefined) continue
    const s = suggested[domain]
    if (Array.isArray(s) && s.length) {
      form.value['site_address_mapping_' + domain] = [...s]
    }
  }
})
</script>

<style scoped>
.config-page {
  padding: 16px;
}
</style>
