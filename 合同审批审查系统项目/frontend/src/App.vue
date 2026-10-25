<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from './api'

const section = ref('tasks')
const statusFilter = ref('')
const tasks = ref([])
const selected = ref(null)
const detail = ref(null)
const rules = ref([])
const logs = ref([])
const summary = reactive({ total: 0, by_status: {}, rules: 0 })
const loading = ref(false)
const error = ref('')
const notice = ref('')

const riskText = { low: '低', medium: '中', high: '高' }
const statusText = {
  pending: '待预处理',
  parsing: '解析中',
  ready: '待审查',
  reviewing: '审查中',
  blocked: '阻塞',
  done: '已完成',
}

const filteredTasks = computed(() =>
  statusFilter.value
    ? tasks.value.filter((item) => item.task_status === statusFilter.value)
    : tasks.value,
)

async function execute(action) {
  loading.value = true
  error.value = ''
  try {
    await action()
  } catch (cause) {
    error.value = cause.message
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  const data = await api.summary()
  summary.total = data.total
  summary.by_status = data.by_status || {}
  summary.rules = data.rules
}

async function loadTasks() {
  tasks.value = await api.tasks(statusFilter.value)
}

async function loadAll() {
  await execute(async () => {
    await Promise.all([loadSummary(), loadTasks(), loadRules()])
  })
}

async function selectTask(task) {
  selected.value = task
  await execute(async () => {
    detail.value = await api.task(task.id)
  })
}

async function pull() {
  await execute(async () => {
    const result = await api.pull()
    notice.value = `拉取完成：新建 ${result.created}，更新 ${result.updated}，预处理 ${result.processed}`
    await Promise.all([loadSummary(), loadTasks()])
  })
}

async function retryTask(task) {
  await execute(async () => {
    await api.retry(task.id)
    notice.value = `任务 ${task.approval_code} 已重试`
    await Promise.all([loadSummary(), loadTasks()])
    await selectTask(task)
  })
}

async function reviewTask(task) {
  await execute(async () => {
    await api.review(task.id)
    notice.value = `任务 ${task.approval_code} 已触发审查`
    await Promise.all([loadSummary(), loadTasks()])
    await selectTask(task)
  })
}

async function toggleStandard(task) {
  await execute(async () => {
    await api.setStandard(task.id, !task.is_standard_version)
    await selectTask(task)
  })
}

async function loadRules() {
  rules.value = await api.rules()
}

async function toggleRule(rule) {
  await execute(async () => {
    const payload = { ...rule, rule_status: rule.rule_status === 'enabled' ? 'disabled' : 'enabled' }
    delete payload.id
    await api.updateRule(rule.id, payload)
    await loadRules()
  })
}

async function loadLogs() {
  await execute(async () => {
    logs.value = await api.logs(selected.value?.id || '')
  })
}

onMounted(loadAll)
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-dot"></span>
        <div>
          <h1>合同审批审查后台</h1>
          <small>Contract Review Service · 8001</small>
        </div>
      </div>
      <nav class="nav">
        <button :class="{ active: section === 'tasks' }" @click="section = 'tasks'">任务监控</button>
        <button :class="{ active: section === 'rules' }" @click="section = 'rules'; loadRules()">规则配置</button>
        <button :class="{ active: section === 'logs' }" @click="section = 'logs'; loadLogs()">运行日志</button>
      </nav>
      <div class="top-actions">
        <button class="ghost" :disabled="loading" @click="loadAll">刷新</button>
        <button class="primary" :disabled="loading" @click="pull">拉取待办</button>
      </div>
    </header>

    <main class="layout">
      <aside class="sidebar">
        <div class="side-title">状态概览</div>
        <button
          class="side-item"
          :class="{ active: statusFilter === '' }"
          @click="statusFilter = ''"
        >
          <span>全部任务</span><b>{{ summary.total }}</b>
        </button>
        <button
          v-for="(count, status) in summary.by_status"
          :key="status"
          class="side-item"
          :class="{ active: statusFilter === status }"
          @click="statusFilter = status"
        >
          <span>{{ statusText[status] || status }}</span><b>{{ count }}</b>
        </button>
        <div class="side-note">
          <p>规则数：{{ summary.rules }}</p>
          <p>模型：确定性规则优先，Milvus 未启用时降级。</p>
        </div>
      </aside>

      <section class="content">
        <div v-if="error" class="alert error">{{ error }}</div>
        <div v-if="notice" class="alert notice">{{ notice }}</div>

        <template v-if="section === 'tasks'">
          <div class="cards">
            <div class="card"><span>任务总数</span><strong>{{ summary.total }}</strong></div>
            <div class="card"><span>已完成</span><strong>{{ summary.by_status.done || 0 }}</strong></div>
            <div class="card"><span>阻塞</span><strong class="danger">{{ summary.by_status.blocked || 0 }}</strong></div>
            <div class="card"><span>规则数</span><strong>{{ summary.rules }}</strong></div>
          </div>

          <div class="split">
            <div class="panel table-panel">
              <div class="panel-head">
                <h2>审批任务</h2>
                <span class="muted">{{ filteredTasks.length }} 条</span>
              </div>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>审批编号</th>
                      <th>标题</th>
                      <th>状态</th>
                      <th>风险</th>
                      <th>版本</th>
                      <th>更新时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="task in filteredTasks"
                      :key="task.id"
                      :class="{ selected: selected?.id === task.id }"
                      @click="selectTask(task)"
                    >
                      <td class="mono">{{ task.approval_code }}</td>
                      <td>{{ task.approval_title }}</td>
                      <td><span class="badge" :class="task.task_status">{{ statusText[task.task_status] }}</span></td>
                      <td><span v-if="detail && selected?.id === task.id && detail.result" class="badge" :class="detail.result.overall_risk_level">{{ riskText[detail.result.overall_risk_level] }}</span><span v-else class="muted">—</span></td>
                      <td class="mono">v{{ task.attachment_version }}</td>
                      <td class="mono">{{ task.updated_at?.slice(0, 19).replace('T', ' ') }}</td>
                    </tr>
                    <tr v-if="!filteredTasks.length">
                      <td colspan="6" class="empty">暂无任务，点击右上角“拉取待办”。</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div v-if="detail" class="panel detail-panel">
              <div class="panel-head">
                <h2>{{ detail.task.approval_title }}</h2>
                <span class="mono">{{ detail.task.approval_code }}</span>
              </div>

              <div class="kv">
                <div><span>状态</span><b>{{ statusText[detail.task.task_status] }}</b></div>
                <div><span>附件版本</span><b>v{{ detail.task.attachment_version }}</b></div>
                <div><span>扫描件</span><b>{{ detail.task.is_scanned ? '是' : '否' }}</b></div>
                <div><span>写回</span><b>{{ detail.task.write_status }}</b></div>
                <div><span>失败步骤</span><b>{{ detail.task.failed_stage || '—' }}</b></div>
                <div><span>重试次数</span><b>{{ detail.task.retry_count }}</b></div>
              </div>

              <div class="actions">
                <button v-if="detail.task.task_status === 'blocked'" class="primary" :disabled="loading" @click="retryTask(detail.task)">从失败步骤重试</button>
                <button class="ghost" :disabled="loading" @click="reviewTask(detail.task)">触发审查</button>
                <button class="ghost" :disabled="loading" @click="toggleStandard(detail.task)">
                  {{ detail.task.is_standard_version ? '取消标准版' : '标记标准版' }}
                </button>
              </div>

              <div v-if="detail.result" class="result-box">
                <div class="result-head">
                  <span>整体风险</span>
                  <span class="badge" :class="detail.result.overall_risk_level">{{ riskText[detail.result.overall_risk_level] }}</span>
                </div>
                <p>{{ detail.result.summary_text }}</p>
                <div class="focus">
                  <div v-for="(point, index) in detail.result.focus_points" :key="index">{{ index + 1 }}. {{ point }}</div>
                </div>
              </div>

              <h3>附件</h3>
              <div class="list">
                <div v-for="file in detail.attachments" :key="file.id" class="list-row">
                  <span>{{ file.file_name }}</span>
                  <span class="muted mono">v{{ file.attachment_version }} · {{ file.file_size }} B</span>
                </div>
                <div v-if="!detail.attachments.length" class="muted">暂无附件</div>
              </div>

              <h3>规则命中（{{ detail.hits.length }}）</h3>
              <div class="list">
                <div v-for="hit in detail.hits" :key="hit.id" class="hit">
                  <div class="hit-head">
                    <b>{{ hit.rule_name || hit.rule_code }}</b>
                    <span class="badge" :class="hit.risk_level">{{ riskText[hit.risk_level] }}</span>
                  </div>
                  <p>{{ hit.evidence_text }}</p>
                  <div class="muted mono">{{ hit.evidence_position }} · {{ hit.suggestion_text }}</div>
                </div>
                <div v-if="!detail.hits.length" class="muted">未命中规则</div>
              </div>

              <h3>条款（{{ detail.parse?.clauses?.length || 0 }}）</h3>
              <div class="list">
                <div v-for="clause in (detail.parse?.clauses || []).slice(0, 5)" :key="clause.clause_no" class="clause">
                  <b>{{ clause.clause_no }} {{ clause.title }}</b>
                  <p>{{ clause.text.slice(0, 160) }}</p>
                </div>
              </div>

              <h3>运行日志</h3>
              <div class="list">
                <div v-for="log in detail.logs.slice(0, 8)" :key="log.id" class="log-row">
                  <span class="mono">{{ log.skill_code }}/{{ log.tool_name }}</span>
                  <span class="muted">{{ log.result_status }} · {{ log.duration_ms }}ms</span>
                </div>
              </div>
            </div>
          </div>
        </template>

        <template v-if="section === 'rules'">
          <div class="panel">
            <div class="panel-head"><h2>PG 规则库</h2><span class="muted">{{ rules.length }} 条</span></div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr><th>编码</th><th>名称</th><th>分类</th><th>等级</th><th>匹配</th><th>状态</th><th>操作</th></tr>
                </thead>
                <tbody>
                  <tr v-for="rule in rules" :key="rule.id">
                    <td class="mono">{{ rule.rule_code }}</td>
                    <td>{{ rule.rule_name }}</td>
                    <td>{{ rule.rule_category }}</td>
                    <td><span class="badge" :class="rule.risk_level">{{ riskText[rule.risk_level] }}</span></td>
                    <td class="mono">{{ rule.match_mode }}<span v-if="rule.target_field"> · {{ rule.target_field }} {{ rule.operator }} {{ rule.threshold_value }}</span></td>
                    <td><span class="badge" :class="rule.rule_status">{{ rule.rule_status }}</span></td>
                    <td><button class="ghost small" :disabled="loading" @click="toggleRule(rule)">{{ rule.rule_status === 'enabled' ? '停用' : '启用' }}</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>

        <template v-if="section === 'logs'">
          <div class="panel">
            <div class="panel-head"><h2>全链路日志</h2><button class="ghost small" @click="loadLogs">刷新</button></div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr><th>时间</th><th>任务</th><th>Skill</th><th>Tool</th><th>状态</th><th>耗时</th><th>内容</th></tr>
                </thead>
                <tbody>
                  <tr v-for="log in logs" :key="log.id">
                    <td class="mono">{{ log.created_at?.slice(11, 19) }}</td>
                    <td class="mono">{{ log.task_id }}</td>
                    <td class="mono">{{ log.skill_code }}</td>
                    <td class="mono">{{ log.tool_name }}</td>
                    <td><span class="badge" :class="log.result_status">{{ log.result_status }}</span></td>
                    <td class="mono">{{ log.duration_ms }}ms</td>
                    <td class="log-content">{{ log.log_content }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>
      </section>
    </main>
  </div>
</template>
