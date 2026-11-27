<template>
  <section class="fx-panel" data-testid="compute-definition-panel">
    <div class="fx-panel-head">
      <div>
        <h3>作业口径读回 · 开始时快照与格式防混读</h3>
        <p class="fx-sub">
          已完成和已认领的作业都能读回开始时绑定的具体口径；每个作业只绑定一种公式格式（text-v1 或 structured-v2），另一种格式读取会被拒绝。
        </p>
      </div>
      <div class="fx-toolbar">
        <input v-model.number="jobIdInput" type="number" min="1" placeholder="输入作业 ID"
               data-testid="definition-job-input"
               style="padding:6px 8px;border:1px solid var(--border);border-radius:6px;width:130px" />
        <button class="fx-btn primary" data-testid="read-definition" type="button" @click="read(jobIdInput)">读回口径</button>
        <select v-model="wantFormat" data-testid="definition-format-select"
                style="padding:6px 8px;border:1px solid var(--border);border-radius:6px">
          <option value="">按作业自身格式</option>
          <option value="text-v1">要求旧文本 text-v1</option>
          <option value="structured-v2">要求结构化 v2</option>
        </select>
      </div>
    </div>

    <p v-if="message" class="fx-msg" :class="messageOk ? 'ok' : 'err'" data-testid="definition-message">{{ message }}</p>

    <div class="fx-toolbar" style="margin-bottom:10px">
      <span class="fx-sub">本指标相关作业快捷读回：</span>
      <button v-for="j in relatedJobs" :key="j.id" class="fx-btn" type="button"
              :data-testid="`quick-def-${j.id}`" @click="read(j.id)">
        #{{ j.id }} {{ j.指标编码 }}/{{ j.计算窗口 }}
        <span class="fx-badge" :class="j.口径格式 === 'structured-v2' ? 'info' : 'warn'">{{ j.口径格式 || 'text-v1' }}</span>
      </button>
    </div>

    <div v-if="definition" :data-testid="`definition-view-${definition.job_id}`">
      <div class="fx-kv" style="grid-template-columns:150px 1fr">
        <dt>作业</dt><dd>#{{ definition.job_id }} {{ definition.作业名称 }}</dd>
        <dt>指标/窗口</dt><dd>{{ definition.metric_code }} / {{ definition.window }}</dd>
        <dt>作业状态</dt><dd>{{ definition.job_status }}（领取：{{ definition.claim_status }}）</dd>
        <dt>绑定格式</dt><dd><span class="fx-badge" :class="definition.format === 'structured-v2' ? 'info' : 'warn'"
          data-testid="definition-format">{{ definition.口径格式 }} · {{ definition.version }}</span></dd>
        <dt>证据包</dt><dd class="fx-mono">{{ definition.evidence_pack_id || '—' }} {{ definition.evidence_hash || '' }}</dd>
      </div>
      <div class="fx-sub" style="margin:8px 0 4px">开始时固化的口径快照（真实公式与事件依赖）</div>
      <pre class="fx-formula" data-testid="definition-snapshot">{{ JSON.stringify(definition.口径快照 || definition.definition, null, 2) }}</pre>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import { listEntries } from '@/api/backend-service'
import { fetchJobDefinition } from '@/api/migration'

const props = defineProps<{ focusCode?: string; refreshTick?: number }>()

const jobIdInput = ref<number | ''>('')
const wantFormat = ref('')
const definition = ref<any>(null)
const message = ref('')
const messageOk = ref(true)
const relatedJobs = ref<any[]>([])

async function read(jobId: number | string) {
  if (!jobId) {
    messageOk.value = false
    message.value = '请先填写作业 ID'
    return
  }
  message.value = ''
  const r = await fetchJobDefinition(jobId, wantFormat.value || undefined)
  if (!r.ok) {
    definition.value = null
    messageOk.value = false
    message.value = r.message || '读取被拒绝'
    return
  }
  definition.value = r
  messageOk.value = true
  message.value = r.message || '已读回作业口径'
}

async function loadRelated() {
  try {
    const page = await listEntries('metric_compute', {})
    relatedJobs.value = (page.items || []).filter((j: any) =>
      props.focusCode ? j.指标编码 === props.focusCode : (j.指标编码 === 'M' || j.口径格式),
    ).slice(-12).reverse()
  } catch {
    relatedJobs.value = []
  }
}

watch(() => props.refreshTick, () => void loadRelated())
onMounted(loadRelated)
</script>
