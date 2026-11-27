<template>
  <section class="page" data-testid="migration-workbench">
    <header class="page-head">
      <div>
        <h2>公式结构化 v2 迁移工作台</h2>
        <p class="page-desc">
          可查每条文本公式的兼容结果与切换进度；服务端按真实事件与分母生成切换检查单，窗口调度器据此领取新版口径作业。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="wb-refresh" type="button" @click="load">刷新工作台</button>
        <button class="btn ghost" data-testid="wb-reset" type="button" @click="reset">重置场景</button>
      </div>
    </header>

    <div class="fx-stat-row" data-testid="summary-row">
      <div class="fx-stat"><b data-testid="stat-total">{{ wb.summary?.metric_total ?? '—' }}</b><span>口径公式总数</span></div>
      <div class="fx-stat"><b class="fx-check-ok">{{ wb.summary?.convertible ?? '—' }}</b><span>可无损转换</span></div>
      <div class="fx-stat"><b style="color:#b42318">{{ wb.summary?.blocked ?? '—' }}</b><span>无法转换</span></div>
      <div class="fx-stat"><b>{{ wb.summary?.unconfirmed ?? '—' }}</b><span>进度未确认</span></div>
      <div class="fx-stat"><b class="fx-check-ok">{{ wb.summary?.online ?? '—' }}</b><span>v2 已上线</span></div>
      <div class="fx-stat"><b>{{ wb.summary?.mapping_total ?? '—' }}</b><span>新旧映射</span></div>
    </div>

    <!-- 题述场景演练：指标 M / 窗口 W -->
    <section class="fx-panel" data-testid="scenario-panel">
      <div class="fx-panel-head">
        <div>
          <h3>切换演练：指标 M · 窗口 W</h3>
          <p class="fx-sub">
            初始：M 的旧版 W 最新一笔计算已登记（text-v1，可重读）。未确认切换进度时领取 W 会冲突；
            上线后领取 W 即把作业口径切换为结构化 v2；随后模拟回滚重跑，W 只确认一次、同一计划窗口只有一个有效结果。
            指标 M2 含未知事件表达式，显示无法转换且不派新版作业。
          </p>
        </div>
      </div>

      <div class="fx-toolbar" style="margin-bottom:10px">
        <button class="fx-btn" data-testid="scenario-precheck" type="button" @click="step(precheckM)">1. 预查 M / M2</button>
        <button class="fx-btn primary" data-testid="scenario-claim-conflict" type="button" @click="step(claimBeforeConfirm)">2. 未确认先领取 W（应冲突）</button>
        <button class="fx-btn primary" data-testid="scenario-golive" type="button" @click="step(goLive)">3. 上线并确认切换</button>
        <button class="fx-btn primary" data-testid="scenario-claim" type="button" @click="step(claimW)">4. 领取 W（切换 v2）</button>
        <button class="fx-btn" data-testid="scenario-confirm" type="button" @click="step(confirmW)">5. 确认 W 结果</button>
        <button class="fx-btn danger" data-testid="scenario-rollback-rerun" type="button" @click="step(rollbackRerun)">6. 模拟回滚重跑</button>
        <button class="fx-btn" data-testid="scenario-confirm-again" type="button" @click="step(confirmWAgain)">7. 再次确认 W（不重复）</button>
      </div>

      <p v-if="scenarioMessage" class="fx-msg" :class="scenarioOk ? 'ok' : 'err'" data-testid="scenario-message">{{ scenarioMessage }}</p>

      <div class="fx-two-col">
        <div class="fx-kv" data-testid="scenario-state">
          <dt>M 切换进度</dt><dd :data-testid="scenario.migration_confirmed ? 'm-online' : 'm-unconfirmed'">
            {{ scenario.migration_confirmed ? '已确认·v2 已上线' : '迁移进度未确认' }}（{{ scenario.migration_progress }}）
          </dd>
          <dt>旧版 W 计算</dt><dd>作业 #{{ scenario.old_job_id }}，旧版仍可重读：{{ scenario.old_version_readable ? '是' : '否' }}</dd>
          <dt>W 领取作业</dt><dd :data-testid="`scenario-w-job`">#{{ scenario.w_job_id ?? '未领取' }}，阶段 {{ scenario.w_phase ?? '—' }}</dd>
          <dt>W 切换记录数</dt><dd data-testid="scenario-switch-count">{{ scenario.w_switch_records }}</dd>
          <dt>W 有效结果数</dt><dd data-testid="scenario-result-count">{{ scenario.w_valid_result_count }}</dd>
          <dt>W 确认次数</dt><dd data-testid="scenario-confirm-count">{{ scenario.w_confirm_count }}</dd>
          <dt>M2 可转换</dt><dd data-testid="scenario-m2-convertible">{{ scenario.m2_convertible ? '是' : '否（无法转换）' }}</dd>
          <dt>M2 新版作业</dt><dd data-testid="scenario-m2-dispatched">{{ scenario.m2_new_job_dispatched ? '已派发' : '未派发' }}</dd>
        </div>
        <div>
          <div class="fx-sub">最近一次接口返回</div>
          <pre class="fx-formula" data-testid="scenario-last-json">{{ lastResult }}</pre>
        </div>
      </div>
    </section>

    <div class="fx-tabs">
      <button v-for="t in tabs" :key="t.key" class="fx-tab" :class="{ active: tab === t.key }"
              :data-testid="`tab-${t.key}`" type="button" @click="tab = t.key">{{ t.label }}</button>
    </div>

    <CatalogMigrationPanel v-if="tab === 'catalog' || tab === 'all'" :refresh-tick="tick" @changed="bump" />
    <SchedulerMigrationPanel v-if="tab === 'scheduler' || tab === 'all'" :refresh-tick="tick" @changed="bump" />
    <ComputeDefinitionPanel v-if="tab === 'jobs' || tab === 'all'" :refresh-tick="tick" />

    <section v-if="tab === 'mappings' || tab === 'all'" class="fx-panel" data-testid="mappings-panel">
      <div class="fx-panel-head"><h3>新旧版本可核对映射</h3>
        <button class="fx-btn" type="button" @click="loadMappings">刷新映射</button>
      </div>
      <table class="fx-table">
        <thead><tr><th>映射编号</th><th>指标</th><th>旧版文本公式</th><th>新版结构</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="mp in mappings" :key="mp.mapping_id" :data-testid="`mapping-row-${mp.metric_code}`">
            <td class="fx-mono">{{ mp.mapping_id }}</td>
            <td>{{ mp.metric_code }}</td>
            <td class="fx-mono">{{ mp.old?.formula_text }}（{{ mp.old?.version }}/{{ mp.old?.format }}）</td>
            <td><pre class="fx-formula">{{ JSON.stringify(mp.structured_formula, null, 2) }}</pre></td>
            <td><span class="fx-badge ok">{{ mp.status }}</span></td>
          </tr>
          <tr v-if="!mappings.length"><td colspan="5" class="fx-sub">确认切换后在此生成映射</td></tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import CatalogMigrationPanel from '@/components/CatalogMigrationPanel.vue'
import ComputeDefinitionPanel from '@/components/ComputeDefinitionPanel.vue'
import SchedulerMigrationPanel from '@/components/SchedulerMigrationPanel.vue'
import {
  claimWindow,
  confirmSwitch,
  confirmWindow,
  fetchMappings,
  fetchWorkbench,
  precheckMetric,
  resetScenario,
  rollbackRerunWindow,
} from '@/api/migration'

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'catalog', label: '口径预查/检查单' },
  { key: 'scheduler', label: '调度领取/回滚' },
  { key: 'jobs', label: '作业口径读回' },
  { key: 'mappings', label: '新旧映射' },
] as const

const tab = ref<(typeof tabs)[number]['key']>('all')
const wb = ref<any>({})
const scenario = ref<any>({})
const mappings = ref<any[]>([])
const tick = ref(0)
const scenarioMessage = ref('')
const scenarioOk = ref(true)
const lastResult = ref('{}')

function showResult(r: any) {
  lastResult.value = JSON.stringify(r, null, 2)
  scenarioOk.value = !!r.ok
  scenarioMessage.value = r.message || (r.ok ? '步骤完成' : '步骤被阻断')
}

async function load() {
  wb.value = await fetchWorkbench()
  scenario.value = wb.value.scenario || {}
  mappings.value = wb.value.mappings || []
}

function bump() {
  tick.value += 1
  void load()
}

async function step(fn: () => Promise<any>) {
  try {
    const r = await fn()
    showResult(r)
  } catch (e) {
    scenarioOk.value = false
    scenarioMessage.value = e instanceof Error ? e.message : '请求失败'
  } finally {
    await load()
    tick.value += 1
  }
}

const precheckM = () => precheckMetric('M')
const claimBeforeConfirm = () => claimWindow('M', 'W')
const goLive = () => confirmSwitch('M')
const claimW = () => claimWindow('M', 'W')
const confirmW = () => confirmWindow('M', 'W')
const confirmWAgain = () => confirmWindow('M', 'W')
const rollbackRerun = () => rollbackRerunWindow('M', 'W')

async function loadMappings() {
  const r = await fetchMappings()
  mappings.value = r.items || []
}

async function reset() {
  await resetScenario()
  scenarioMessage.value = '场景已重置：M/W 回到「旧版已登记、迁移未确认」初始态'
  scenarioOk.value = true
  lastResult.value = '{}'
  await load()
  tick.value += 1
}

onMounted(load)
</script>
