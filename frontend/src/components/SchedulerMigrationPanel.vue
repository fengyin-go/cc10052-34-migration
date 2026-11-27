<template>
  <section class="fx-panel" data-testid="scheduler-migration-panel">
    <div class="fx-panel-head">
      <div>
        <h3>窗口调度 · 新版口径作业领取与切换状态</h3>
        <p class="fx-sub">
          调度器依据服务端切换检查单领取作业：迁移进度未确认会与领取冲突；只能领取可执行计算作业关联的同一套口径证据。
        </p>
      </div>
      <div class="fx-toolbar">
        <button class="fx-btn" data-testid="scheduler-reload" type="button" @click="load">刷新</button>
        <button class="fx-btn primary" data-testid="batch-dispatch" type="button" @click="dispatch">
          批量办理到期窗口领取
        </button>
      </div>
    </div>

    <p v-if="message" class="fx-msg" :class="messageOk ? 'ok' : 'err'" data-testid="scheduler-message">{{ message }}</p>

    <div class="fx-toolbar" style="margin-bottom: 10px">
      <label class="fx-sub">指标编码
        <input v-model="form.metric_code" data-testid="claim-metric-input" style="margin-left:6px;padding:5px 8px;border:1px solid var(--border);border-radius:6px" />
      </label>
      <label class="fx-sub">计划窗口
        <input v-model="form.window" data-testid="claim-window-input" style="margin-left:6px;width:90px;padding:5px 8px;border:1px solid var(--border);border-radius:6px" />
      </label>
      <button class="fx-btn primary" data-testid="claim-window" type="button" @click="claim">领取窗口作业</button>
      <button class="fx-btn" data-testid="complete-window" type="button" @click="complete">完成计算</button>
      <button class="fx-btn" data-testid="confirm-window" type="button" @click="confirm">确认结果（仅一次）</button>
      <button class="fx-btn danger" data-testid="rollback-window" type="button" @click="rollback">回滚窗口</button>
      <button class="fx-btn" data-testid="rerun-window" type="button" @click="rerun">回滚后续做/重跑</button>
      <button class="fx-btn danger" data-testid="rollback-rerun-window" type="button" @click="rollbackRerun">模拟回滚重跑</button>
    </div>

    <div class="fx-two-col">
      <div>
        <h4 style="margin:6px 0">当前切换状态 vs 窗口实际使用版本</h4>
        <table class="fx-table" data-testid="window-table">
          <thead>
            <tr>
              <th>指标/窗口</th>
              <th>当前切换状态（口径中心）</th>
              <th>窗口实际使用版本（作业快照）</th>
              <th>阶段/确认</th>
              <th>证据与结果</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="w in windows" :key="w.metric_code + w.window" :data-testid="`window-row-${w.metric_code}-${w.window}`">
              <td>
                <b>{{ w.metric_code }}</b> / {{ w.window }}
                <div class="fx-sub">作业 #{{ w.job_id }}；旧版作业 #{{ w.old_job_id }}</div>
              </td>
              <td>
                <span class="fx-badge info" :data-testid="`switch-state-${w.metric_code}-${w.window}`">{{ w.当前切换状态 }}</span>
              </td>
              <td>
                <span class="fx-badge ok" :data-testid="`actual-version-${w.metric_code}-${w.window}`">{{ w.窗口实际使用版本 }}</span>
                <div class="fx-sub">同窗口旧计算：{{ w.old_window_version }}</div>
              </td>
              <td>
                <span class="fx-badge warn">{{ w.phase }}</span>
                <div class="fx-sub" :data-testid="`confirm-count-${w.metric_code}-${w.window}`">
                  确认次数 {{ w.confirmation?.confirm_count || 0 }}；重跑 {{ w.rerun_count }} 次；回滚 {{ w.rollback_history?.length || 0 }} 次
                </div>
              </td>
              <td>
                <div class="fx-mono">{{ w.pack_id }}</div>
                <div v-if="w.result" :data-testid="`valid-result-${w.metric_code}-${w.window}`">
                  有效结果：{{ w.result.result_id }} = {{ w.result.value }}
                </div>
                <div v-else class="fx-sub">尚无有效结果</div>
              </td>
            </tr>
            <tr v-if="!windows.length"><td colspan="5" class="fx-sub">尚无窗口领取记录</td></tr>
          </tbody>
        </table>
      </div>

      <div>
        <h4 style="margin:6px 0">调度批量办理结果（含不派新版作业的指标）</h4>
        <div v-if="dispatchResult" data-testid="dispatch-result">
          <p class="fx-msg ok">{{ dispatchResult.message }}</p>
          <div v-for="s in dispatchResult.skipped" :key="s.metric_code + s.window" class="fx-blocker" style="margin-bottom:6px"
               :data-testid="`dispatch-skip-${s.metric_code}`">
            <b>{{ s.metric_code }}/{{ s.window }}</b> 不派新版作业：{{ s.reason }}
          </div>
          <div v-for="d in dispatchResult.dispatched" :key="d.metric_code + d.window" class="fx-ref-item">
            {{ d.metric_code }}/{{ d.window }} → 作业 #{{ d.job_id }}，批次 {{ d.batch_id }}
          </div>
        </div>
        <div v-else class="fx-sub">点击「批量办理到期窗口领取」查看：无法转换（如 M2）或进度未确认的指标会被跳过且不派新版作业。</div>

        <h4 style="margin:12px 0 6px">作业批次：真实公式与事件依赖</h4>
        <div v-for="b in batches" :key="b.batch_id" class="fx-ref-item" :data-testid="`batch-${b.batch_id}`">
          <b>批次 {{ b.batch_id }}</b>（证据 {{ b.pack_id }}）
          <div v-for="j in b.jobs" :key="j.job_id" style="margin-top:4px">
            作业 #{{ j.job_id }} {{ j.metric_code }}/{{ j.window }}
            <div class="fx-mono">真实公式：{{ j.real_formula || j.definition?.real_formula?.expression || '—' }}</div>
            <div>
              事件依赖：
              <span v-for="e in (j.event_dependencies?.length ? j.event_dependencies : j.definition?.event_dependencies || [])"
                    :key="e.event_code" class="fx-badge info" style="margin-right:4px">{{ e.event_code }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'

import {
  claimWindow as apiClaim,
  completeWindow as apiComplete,
  confirmWindow as apiConfirm,
  dispatchScheduler,
  fetchWorkbench,
  rollbackRerunWindow as apiRollbackRerun,
  rollbackWindow as apiRollback,
  rerunWindow as apiRerun,
} from '@/api/migration'

const props = defineProps<{ focusCode?: string; refreshTick?: number }>()
const emit = defineEmits<{ changed: [] }>()

const windows = ref<any[]>([])
const batches = ref<any[]>([])
const dispatchResult = ref<any>(null)
const message = ref('')
const messageOk = ref(true)
const form = reactive({ metric_code: props.focusCode || 'M', window: 'W' })

function flash(ok: boolean, text: string) {
  messageOk.value = ok
  message.value = text
}

async function load() {
  try {
    const wb = await fetchWorkbench()
    windows.value = props.focusCode
      ? (wb.windows || []).filter((w: any) => w.metric_code === props.focusCode)
      : wb.windows || []
    batches.value = wb.batches || []
  } catch (e) {
    messageOk.value = false
    message.value = e instanceof Error ? e.message : '工作台读取失败'
  }
}

async function run(fn: () => Promise<any>) {
  message.value = ''
  try {
    const r = await fn()
    flash(!!r.ok, r.message || (r.ok ? '操作完成' : '操作被拒绝'))
    await load()
    emit('changed')
    return r
  } catch (e) {
    flash(false, e instanceof Error ? e.message : '操作失败')
  }
}

const claim = () => run(() => apiClaim(form.metric_code, form.window))
const complete = () => run(() => apiComplete(form.metric_code, form.window))
const confirm = () => run(() => apiConfirm(form.metric_code, form.window))
const rollback = () => run(() => apiRollback(form.metric_code, form.window))
const rerun = () => run(() => apiRerun(form.metric_code, form.window))
const rollbackRerun = () => run(() => apiRollbackRerun(form.metric_code, form.window))
const dispatch = () => run(async () => {
  dispatchResult.value = await dispatchScheduler()
  return dispatchResult.value
})

watch(() => props.refreshTick, () => void load())
watch(() => props.focusCode, (v) => { if (v) form.metric_code = v })
onMounted(load)
</script>
