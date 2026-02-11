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
        <div v-if="sitesForMapping.length" class="mb-4">
          <div class="text-h6 mb-2">站点地址映射</div>
          <div class="text-body-2 mb-3 text-grey">
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
        </div>
        <v-btn color="primary" @click="save" :loading="saving">保存配置</v-btn>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'

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

const saving = ref(false)

const sitesForMapping = computed(() => {
  const selected = new Set((form.value.selected_sites as string[]) || [])
  const withData = new Set(formOptions.value.sites_with_config_data || [])
  return formOptions.value.sites.filter(s => selected.has(s.value) && withData.has(s.value))
})

function getSiteMapping(domain: string): string[] {
  const v = form.value['site_address_mapping_' + domain]
  return Array.isArray(v) ? [...v] : []
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
    sync_downloaders: form.value.sync_downloaders,
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
  form.value = {
    selected_sites: Array.isArray(init.selected_sites) ? [...init.selected_sites] : [],
    sync_downloaders: Array.isArray(init.sync_downloaders) ? [...init.sync_downloaders] : [],
  }
  for (const [k, v] of Object.entries(init)) {
    if (k.startsWith('site_address_mapping_')) {
      form.value[k] = v
    }
  }
  await fetchFormOptions()
})
</script>

<style scoped>
.config-page {
  padding: 16px;
}
</style>
