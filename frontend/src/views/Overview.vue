<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>

    <RouterLink to="/migration" class="fx-panel" data-testid="overview-migration-entry"
      style="display:flex;align-items:center;justify-content:space-between;text-decoration:none;color:inherit;border-color:#b6ccf7;background:#f5f9ff">
      <div>
        <h3 style="margin:0 0 4px">公式结构化 v2 迁移工作台</h3>
        <p class="fx-sub" style="margin:0">
          预查指标口径中心每条文本公式的兼容性与阻断原因，核对新旧映射、切换检查单与进度，
          办理窗口 W 的新版作业领取、确认（仅一次）与回滚续做。
        </p>
      </div>
      <span class="btn primary" style="flex-shrink:0;margin-left:16px">进入工作台 →</span>
    </RouterLink>

    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
    moduleRows.value = [{"name": "事件接入", "created": 0, "pending": 0, "abnormal": 0}, {"name": "实验设计", "created": 0, "pending": 0, "abnormal": 0}, {"name": "分组分配器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "指标口径中心", "created": 0, "pending": 0, "abnormal": 0}, {"name": "指标计算作业", "created": 0, "pending": 0, "abnormal": 0}, {"name": "显著性检验", "created": 0, "pending": 0, "abnormal": 0}, {"name": "归因分析器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "人群圈选器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "报告生成器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "报告分发", "created": 0, "pending": 0, "abnormal": 0}, {"name": "窗口调度器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据时效监控", "created": 0, "pending": 0, "abnormal": 0}, {"name": "指标异常检测", "created": 0, "pending": 0, "abnormal": 0}, {"name": "实验归档库", "created": 0, "pending": 0, "abnormal": 0}, {"name": "样本量核对", "created": 0, "pending": 0, "abnormal": 0}, {"name": "AA预检器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "切片分析器", "created": 0, "pending": 0, "abnormal": 0}, {"name": "曝光日志接入", "created": 0, "pending": 0, "abnormal": 0}, {"name": "结论评审队列", "created": 0, "pending": 0, "abnormal": 0}, {"name": "指标查询服务", "created": 0, "pending": 0, "abnormal": 0}]
  }
})
</script>
