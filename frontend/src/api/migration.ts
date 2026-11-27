/** 结构化公式 v2 迁移工作台接口封装。 */
import { request } from './client'

async function parse<T>(response: Response): Promise<T> {
  return (await response.json()) as T
}

export async function fetchWorkbench(): Promise<any> {
  return parse(await request('/api/formula-v2/workbench'))
}

export async function precheckAll(): Promise<any> {
  return parse(await request('/api/formula-v2/precheck'))
}

export async function precheckMetric(metricCode: string): Promise<any> {
  return parse(
    await request('/api/formula-v2/precheck', {
      method: 'POST',
      body: JSON.stringify({ metric_code: metricCode }),
    }),
  )
}

export async function fetchChecklist(metricCode: string): Promise<any> {
  return parse(await request(`/api/formula-v2/checklist/${encodeURIComponent(metricCode)}`))
}

export async function confirmSwitch(metricCode: string): Promise<any> {
  return parse(
    await request(`/api/formula-v2/metrics/${encodeURIComponent(metricCode)}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ operator: '页面工作台' }),
    }),
  )
}

export async function claimWindow(metricCode: string, window: string): Promise<any> {
  return parse(
    await request('/api/formula-v2/windows/claim', {
      method: 'POST',
      body: JSON.stringify({ metric_code: metricCode, window }),
    }),
  )
}

export async function completeWindow(metricCode: string, window: string): Promise<any> {
  return parse(
    await request('/api/formula-v2/windows/complete', {
      method: 'POST',
      body: JSON.stringify({ metric_code: metricCode, window }),
    }),
  )
}

export async function confirmWindow(metricCode: string, window: string): Promise<any> {
  return parse(
    await request('/api/formula-v2/windows/confirm', {
      method: 'POST',
      body: JSON.stringify({ metric_code: metricCode, window }),
    }),
  )
}

export async function rollbackWindow(metricCode: string, window: string): Promise<any> {
  return parse(
    await request('/api/formula-v2/windows/rollback', {
      method: 'POST',
      body: JSON.stringify({ metric_code: metricCode, window }),
    }),
  )
}

export async function rerunWindow(metricCode: string, window: string): Promise<any> {
  return parse(
    await request('/api/formula-v2/windows/rerun', {
      method: 'POST',
      body: JSON.stringify({ metric_code: metricCode, window }),
    }),
  )
}

export async function rollbackRerunWindow(metricCode: string, window: string): Promise<any> {
  return parse(
    await request('/api/formula-v2/windows/rollback-rerun', {
      method: 'POST',
      body: JSON.stringify({ metric_code: metricCode, window }),
    }),
  )
}

export async function fetchReferences(metricCode: string): Promise<any> {
  return parse(await request(`/api/formula-v2/references/${encodeURIComponent(metricCode)}`))
}

export async function fetchJobDefinition(jobId: number | string, format?: string): Promise<any> {
  const query = format ? `?format=${encodeURIComponent(format)}` : ''
  return parse(await request(`/api/formula-v2/jobs/${jobId}/definition${query}`))
}

export async function fetchMappings(): Promise<any> {
  return parse(await request('/api/formula-v2/mappings'))
}

export async function dispatchScheduler(): Promise<any> {
  return parse(await request('/api/formula-v2/scheduler/dispatch', { method: 'POST' }))
}

export async function resetScenario(): Promise<any> {
  return parse(await request('/api/formula-v2/testing/reset', { method: 'POST' }))
}
