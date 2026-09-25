<template>
  <section class="page" data-module="aeration">
    <header class="page-head">
      <div>
        <h2>曝气控制管理</h2>
        <p class="page-desc">维护曝气记录，围绕记录编号、曝气池编号、溶解氧值、风量设定做登记、筛选与状态流转，并按曝气池编号给出溶解氧-风量联动建议。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记曝气记录</button>
        <button class="btn" type="button" @click="showRules = !showRules">联动规则</button>
        <button class="btn" type="button" @click="exportRows">导出曝气控制清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section v-if="showRules" class="rule-panel">
      <h3 class="rule-title">溶解氧-风量联动规则（按曝气池编号）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>曝气池编号</th>
            <th>溶解氧目标范围</th>
            <th>风量调节阈值</th>
            <th>风机频率上限</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rule in tankRules" :key="rule['曝气池编号']">
            <td>{{ rule['曝气池编号'] }}</td>
            <td>{{ rule['溶解氧下限'] }}–{{ rule['溶解氧上限'] }} mg/L</td>
            <td>{{ rule['风量调节阈值'] }}</td>
            <td>{{ rule['风机频率上限'] }} Hz</td>
          </tr>
          <tr v-if="!tankRules.length">
            <td colspan="4" class="empty-state">联动规则暂未读取到，请稍后重试</td>
          </tr>
        </tbody>
      </table>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>联动建议</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="String(row.id)">
          <tr>
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td>
              <span v-if="suggestionOf(row)" class="suggestion-cell">
                {{ suggestionOf(row)?.建议档位 }}
                <em class="priority-tag" :class="priorityClass(suggestionOf(row)?.优先级)">
                  {{ suggestionOf(row)?.优先级 }}
                </em>
              </span>
              <span v-else>—</span>
            </td>
            <td class="row-actions">
              <button class="link" type="button" @click="toggleSuggestion(row)">联动建议</button>
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="suggestionRowId === Number(row.id)" class="suggestion-row">
            <td :colspan="columns.length + 2">
              <div class="suggestion-panel">
                <p class="suggestion-rule">{{ ruleText(row) }}</p>
                <div class="suggestion-form">
                  <label v-for="field in suggestionFields" :key="field" class="filter-item">
                    <span>{{ field }}</span>
                    <input v-model="suggestionForm[field]" :placeholder="`请输入${field}`" />
                  </label>
                  <button
                    class="btn primary"
                    type="button"
                    :disabled="suggestionBusy"
                    @click="submitSuggestion(row)"
                  >
                    生成联动建议
                  </button>
                </div>
                <p v-if="suggestionMessage" :class="suggestionOk ? 'ok-text' : 'error-text'">
                  {{ suggestionMessage }}
                </p>
                <p v-if="suggestionOf(row)?.建议说明" class="suggestion-note">
                  当前建议：{{ suggestionOf(row)?.建议说明 }}
                </p>
              </div>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无曝气控制数据，可先登记曝气记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条曝气控制记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Suggestion = {
  建议档位?: string
  优先级?: string
  建议说明?: string
  目标范围?: string
  风量调节阈值?: number
}

type Row = Record<string, string | number | null | Suggestion>

type TankRule = {
  曝气池编号: string
  溶解氧下限: number
  溶解氧上限: number
  风量调节阈值: number
  风机频率上限: number
}

const ENDPOINT = '/api/aeration'
const columns = ["记录编号", "曝气池编号", "溶解氧值", "风量设定", "风机频率", "调节时间", "操作人员", "控制状态"]
const actions = ["提交调节", "复核确认", "锁定参数"]
const statuses = ["待调节", "已调节", "待复核", "已锁定"]
const stats = [{"label": "今日调节次数", "value": 0}, {"label": "溶解氧均值", "value": 0}, {"label": "锁定参数项", "value": 0}]
const suggestionFields = ["溶解氧值", "风量设定", "风机频率"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const tankRules = ref<TankRule[]>([])
const showRules = ref(false)
const suggestionRowId = ref<number | null>(null)
const suggestionForm = ref<Record<string, string>>({})
const suggestionMessage = ref('')
const suggestionOk = ref(false)
const suggestionBusy = ref(false)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '曝气记录登记入口尚未接入审批流'
}

function suggestionOf(row: Row): Suggestion | null {
  const value = row['联动建议']
  if (value && typeof value === 'object') {
    return value as Suggestion
  }
  return null
}

function priorityClass(priority: string | undefined): string {
  if (priority === '高') return 'high'
  if (priority === '中') return 'mid'
  return 'low'
}

function ruleForRow(row: Row): TankRule | undefined {
  return (
    tankRules.value.find((rule) => rule['曝气池编号'] === row['曝气池编号']) ??
    tankRules.value.find((rule) => rule['曝气池编号'].includes('默认'))
  )
}

function ruleText(row: Row): string {
  const rule = ruleForRow(row)
  if (!rule) {
    return '当前池号暂未配置联动规则'
  }
  return `池号 ${rule['曝气池编号']}：溶解氧目标 ${rule['溶解氧下限']}–${rule['溶解氧上限']} mg/L，风量调节阈值 ${rule['风量调节阈值']}，风机频率上限 ${rule['风机频率上限']} Hz`
}

function toggleSuggestion(row: Row) {
  const rowId = Number(row.id)
  if (suggestionRowId.value === rowId) {
    suggestionRowId.value = null
    return
  }
  suggestionRowId.value = rowId
  suggestionForm.value = Object.fromEntries(
    suggestionFields.map((field) => [field, row[field] == null ? '' : String(row[field])]),
  )
  suggestionMessage.value = ''
  suggestionOk.value = false
}

async function submitSuggestion(row: Row) {
  suggestionBusy.value = true
  suggestionMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/suggestion`, {
      method: 'POST',
      body: JSON.stringify({ values: suggestionForm.value }),
    })
    const payload = await response.json()
    suggestionOk.value = Boolean(payload?.ok)
    suggestionMessage.value = payload?.message ?? (payload?.ok ? '联动建议已生成' : '联动建议未生成')
    if (payload?.ok) {
      await reload()
    }
  } catch (error) {
    suggestionOk.value = false
    suggestionMessage.value = error instanceof Error ? error.message : '联动建议提交失败'
  } finally {
    suggestionBusy.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '曝气控制动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '曝气控制操作失败'
  }
}

async function loadTankRules() {
  try {
    const response = await request(`${ENDPOINT}/tank-rules`)
    if (!response.ok) {
      return
    }
    const payload = await response.json()
    tankRules.value = payload.items ?? []
  } catch {
    tankRules.value = []
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('曝气记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '曝气控制列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadTankRules()
})
</script>

<style scoped>
.rule-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; }
.rule-title { margin: 0 0 8px; font-size: 14px; }
.suggestion-cell { display: inline-flex; align-items: center; gap: 6px; }
.priority-tag { font-style: normal; font-size: 12px; border-radius: 4px; padding: 1px 6px; }
.priority-tag.high { background: #fee4e2; color: #b42318; }
.priority-tag.mid { background: #fef0c7; color: #b54708; }
.priority-tag.low { background: #e0f2fe; color: #026aa2; }
.suggestion-row td { background: #f8fafc; }
.suggestion-panel { display: flex; flex-direction: column; gap: 8px; }
.suggestion-rule { margin: 0; color: var(--muted); font-size: 12px; }
.suggestion-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; }
.suggestion-note { margin: 0; font-size: 12px; }
.ok-text { margin: 0; font-size: 12px; color: #067647; }
</style>
