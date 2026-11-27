<template>
  <section class="fx-panel" data-testid="catalog-migration-panel">
    <div class="fx-panel-head">
      <div>
        <h3>公式结构化 v2 · 兼容预查与切换检查单</h3>
        <p class="fx-sub">
          服务端按「事件接入」中的真实事件与登记分母解析每条文本公式；不能无损转换的，列出原值与阻断原因。
        </p>
      </div>
      <div class="fx-toolbar">
        <button class="fx-btn" data-testid="fx-reload" type="button" @click="load">刷新预查</button>
        <button class="fx-btn primary" data-testid="fx-precheck-all" type="button" :disabled="loading" @click="precheckAll">
          全部重新预查
        </button>
      </div>
    </div>

    <p v-if="message" class="fx-msg" :class="messageOk ? 'ok' : 'err'" data-testid="fx-message">{{ message }}</p>

    <div class="fx-grid">
      <article
        v-for="m in visibleMetrics"
        :key="m.metric_code + '-' + m.catalog_id"
        class="fx-card"
        :class="{ blocked: !m.convertible, online: m.progress === 'ONLINE' }"
        :data-testid="`metric-card-${m.metric_code}`"
      >
        <div class="fx-card-title">
          <span data-testid="metric-code">{{ m.metric_code }}</span>
          <span class="fx-sub">{{ m.metric_name }}</span>
          <span
            class="fx-badge"
            :class="m.convertible ? 'ok' : 'bad'"
            :data-testid="`compatibility-${m.metric_code}`"
          >{{ m.compatibility }}</span>
          <span class="fx-badge info" :data-testid="`progress-${m.metric_code}`">{{ m.切换进度 }}</span>
        </div>

        <div>
          <div class="fx-sub">原文本公式（{{ m.old_version }}）</div>
          <div class="fx-original" :data-testid="`original-formula-${m.metric_code}`">{{ m.original_formula }}</div>
        </div>

        <template v-if="m.convertible && m.structured_formula">
          <div>
            <div class="fx-sub">结构化公式 v2（事件 / 分母含义保留）</div>
            <pre class="fx-formula" :data-testid="`structured-${m.metric_code}`">{{ JSON.stringify(m.structured_formula, null, 2) }}</pre>
          </div>
          <div class="fx-kv">
            <dt>事件依赖</dt>
            <dd :data-testid="`events-${m.metric_code}`">
              <span v-for="e in m.event_dependencies" :key="e.event_code" class="fx-badge ok" style="margin-right:4px">
                {{ e.event_code }}（{{ e.status }}）
              </span>
            </dd>
            <dt>分母解析</dt>
            <dd>
              <span v-for="d in m.denominator_dependencies" :key="d.code" class="fx-badge info">
                {{ d.label }} → {{ d.code }} · {{ d.source }}
              </span>
            </dd>
            <dt v-if="m.mapping_id">新旧映射</dt>
            <dd v-if="m.mapping_id" class="fx-mono" :data-testid="`mapping-${m.metric_code}`">{{ m.mapping_id }} / {{ m.pack_id }}</dd>
          </div>
        </template>

        <div v-else>
          <div class="fx-sub">不能切换的原因（原值保留在上）</div>
          <div
            v-for="(b, i) in m.blockers"
            :key="i"
            class="fx-blocker"
            :data-testid="`blocker-${m.metric_code}`"
          >
            <span class="fx-code">{{ b.code }}</span>{{ b.reason }}
          </div>
        </div>

        <ul v-if="openChecklist[m.metric_code]" class="fx-checklist" :data-testid="`checklist-${m.metric_code}`">
          <li v-for="(it, i) in m.checklist" :key="i">
            <span class="fx-check-ico" :class="it.ok ? 'fx-check-ok' : 'fx-check-no'">{{ it.ok ? '✓' : '✕' }}</span>
            <span><b>{{ it.label }}</b><br /><span class="fx-sub">{{ it.detail }}</span></span>
          </li>
        </ul>

        <div class="fx-actions">
          <button class="fx-btn" type="button" :data-testid="`toggle-checklist-${m.metric_code}`" @click="toggleChecklist(m.metric_code)">
            {{ openChecklist[m.metric_code] ? '收起检查单' : '查看切换检查单' }}
          </button>
          <button
            v-if="m.convertible && m.progress !== 'ONLINE'"
            class="fx-btn primary"
            type="button"
            :data-testid="`confirm-${m.metric_code}`"
            :disabled="busy === m.metric_code"
            @click="confirm(m.metric_code)"
          >确认切换并上线 v2</button>
          <span v-else-if="m.progress === 'ONLINE'" class="fx-badge ok">已上线，可被调度领取</span>
          <button class="fx-btn" type="button" :data-testid="`refs-${m.metric_code}`" @click="loadReferences(m.metric_code)">
            查证旧公式/旧作业引用
          </button>
        </div>

        <div v-if="refs[m.metric_code]" :data-testid="`reference-box-${m.metric_code}`">
          <div class="fx-sub">旧公式 ↔ 旧作业 ↔ 历史查询引用</div>
          <div v-for="d in refs[m.metric_code].旧公式定义" :key="'d' + d.catalog_id" class="fx-ref-item">
            口径#{{ d.catalog_id }} · {{ d.口径版本 }} · {{ d.status }} · <span class="fx-mono">{{ d.计算口径 }}</span>
          </div>
          <div v-for="j in refs[m.metric_code].旧作业引用" :key="'j' + j.job_id" class="fx-ref-item">
            作业#{{ j.job_id }} · 窗口 {{ j.计算窗口 }} · {{ j.status }} ·
            格式 {{ j.口径格式 || '未快照' }} · {{ j.原始公式 }}
            <a class="fx-mono" :data-testid="`job-def-link-${j.job_id}`" href="javascript:void(0)" @click="readJob(j.job_id)">读回口径</a>
            <pre v-if="jobDefs[j.job_id]" class="fx-formula" style="margin-top:6px">{{ JSON.stringify(jobDefs[j.job_id], null, 2) }}</pre>
          </div>
          <div v-for="q in refs[m.metric_code].历史查询接口" :key="'q' + q.api_id" class="fx-ref-item">
            查询接口 {{ q.接口编码 }} · {{ q.status }} · {{ q.口径版本 }} · <span class="fx-mono">{{ q.原始公式 }}</span>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  confirmSwitch,
  fetchJobDefinition,
  fetchReferences,
  fetchWorkbench,
  precheckAll as apiPrecheckAll,
} from '@/api/migration'

const props = defineProps<{ focusCode?: string; refreshTick?: number }>()
const emit = defineEmits<{ changed: [] }>()

const data = ref<any>({ metrics: [] })
const loading = ref(false)
const busy = ref('')
const message = ref('')
const messageOk = ref(true)
const openChecklist = ref<Record<string, boolean>>({})
const refs = ref<Record<string, any>>({})
const jobDefs = ref<Record<number, any>>({})

const visibleMetrics = computed(() => {
  const items: any[] = data.value.metrics || []
  if (props.focusCode) return items.filter((m) => m.metric_code === props.focusCode)
  return items
})

async function load() {
  loading.value = true
  try {
    data.value = await fetchWorkbench()
  } catch (e) {
    messageOk.value = false
    message.value = e instanceof Error ? e.message : '工作台读取失败'
  } finally {
    loading.value = false
  }
}

async function precheckAll() {
  message.value = ''
  try {
    await apiPrecheckAll()
    await load()
    messageOk.value = true
    message.value = '全部公式已按真实事件与分母重新预查'
  } catch (e) {
    messageOk.value = false
    message.value = e instanceof Error ? e.message : '预查失败'
  }
}

function toggleChecklist(code: string) {
  openChecklist.value[code] = !openChecklist.value[code]
}

async function confirm(code: string) {
  busy.value = code
  message.value = ''
  try {
    const r = await confirmSwitch(code)
    messageOk.value = !!r.ok
    message.value = r.message || (r.ok ? '已确认切换' : '切换失败')
    await load()
    emit('changed')
  } catch (e) {
    messageOk.value = false
    message.value = e instanceof Error ? e.message : '确认失败'
  } finally {
    busy.value = ''
  }
}

async function loadReferences(code: string) {
  refs.value[code] = await fetchReferences(code)
}

async function readJob(jobId: number) {
  jobDefs.value[jobId] = await fetchJobDefinition(jobId)
}

watch(() => props.refreshTick, () => void load())
onMounted(load)
</script>
