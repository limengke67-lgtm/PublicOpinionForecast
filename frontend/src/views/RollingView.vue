<template>
  <div class="rolling-page">
    <header class="topbar">
      <RouterLink to="/" class="brand">MIROFISH <span>/ ADAPTIVE FORECAST</span></RouterLink>
      <RouterLink to="/research">预测验证与费用账本 ↗</RouterLink>
    </header>
    <main>
      <div class="intro">
        <div><p class="eyebrow">真实观测 → 检验旧预测 → 校准 → 更新未来</p><h1>让预测跟上现实。</h1><p>每次更新都有依据，每版预测都能回溯。</p></div>
        <button :disabled="busy" @click="run(createDemo)">创建闭环合成示例 ↗</button>
      </div>
      <nav class="tabs" aria-label="滚动预测功能">
        <button v-for="tab in tabs" :key="tab.id" :aria-pressed="activeTab === tab.id" :class="{ selected: activeTab === tab.id }" @click="activeTab = tab.id">{{ tab.label }}</button>
      </nav>
      <div v-if="error" class="notice error" role="alert">{{ error }}</div>
      <div v-if="message" class="notice success" role="status">{{ message }}</div>

      <section v-if="activeTab === 'workspace'" class="workspace">
        <aside class="panel sidebar">
          <h2>滚动预测任务</h2>
          <p v-if="!sessions.length" class="hint">先创建示例，或选择已有案例和冻结预测。</p>
          <button v-for="item in sessions" :key="item.session_id" class="session-row" :class="{ chosen: session?.session_id === item.session_id }" @click="run(() => selectSession(item.session_id))">
            <strong>{{ item.name }}</strong><span>{{ item.is_demo ? '合成示例' : item.mode === 'live' ? '实际接收' : '历史回放' }} · {{ item.version_count }} 个版本 · {{ item.status === 'active' ? '接收中' : '已暂停' }}</span>
          </button>
          <details :open="!sessions.length">
            <summary>创建新任务</summary>
            <form @submit.prevent="run(createSession)">
              <label>验证案例<select v-model="form.case_id" required @change="run(loadForecasts)"><option value="" disabled>选择已有案例</option><option v-for="item in cases" :key="item.case_id" :value="item.case_id">{{ item.name }}</option></select></label>
              <label>起始冻结预测<select v-model="form.forecast_id" required><option value="" disabled>选择冻结预测</option><option v-for="item in forecasts" :key="item.forecast_id" :value="item.forecast_id">{{ item.variant || item.method }} / {{ item.forecast_id.slice(-6) }}</option></select></label>
              <label>数据接收模式<select v-model="form.mode"><option value="live">实际接收新增数据</option><option value="replay">历史数据回放</option></select></label>
              <details><summary>校准策略</summary>
                <label>连续偏差阈值<input v-model.number="form.policy.trigger_ratio" type="number" min="0.01" max="1" step="0.01" required /></label>
                <label>连续同向偏差桶数<input v-model.number="form.policy.consecutive" type="number" min="1" max="12" required /></label>
                <label>校准窗口桶数<input v-model.number="form.policy.window" type="number" min="3" max="100" required /></label>
                <p class="hint">默认至少 3 个已验证样本。只有此前影子校准预测达到改进门槛时才启用校准；策略创建后固定。</p>
              </details>
              <button class="full" :disabled="busy || !form.forecast_id">创建滚动任务</button>
              <button v-if="form.mode === 'replay'" type="button" class="secondary full" :disabled="busy || !form.forecast_id" @click="run(replaySession)">执行完整历史回放（无模型调用）</button>
            </form>
          </details>
          <RouterLink class="text-link" to="/research">导入案例、运行并冻结初始预测 ↗</RouterLink>
          <label class="restore">恢复任务备份<input type="file" accept=".json" @change="restoreFile" /></label>
        </aside>

        <div v-if="session" class="content">
          <section class="panel">
            <div class="section-top"><div><p class="eyebrow">{{ session.session_id }}</p><h2>{{ session.name }}</h2></div><span class="badge">{{ session.status === 'active' ? '接收中' : '已暂停' }} · {{ session.mode === 'live' ? '实际接收' : '历史回放' }}</span></div>
            <div v-if="session.is_demo" class="notice">合成案例用于验证闭环流程，不能证明真实事件预测能力。</div>
            <p class="hint">当前观测截至 {{ displayTime(latest.cutoff) }} · {{ session.observations.length }} 桶 · 每桶 {{ session.bucket_hours }} 小时 · 预测未来 {{ session.horizon }} 桶</p>
            <div class="button-row">
              <button class="secondary" :disabled="busy" @click="run(toggleStatus)">{{ session.status === 'active' ? '暂停接收' : '恢复接收' }}</button>
              <button class="secondary" :disabled="busy" @click="run(() => exportData('json'))">导出任务备份</button>
              <button class="secondary" :disabled="busy || !session.summary.scored_buckets" @click="run(() => exportData('csv'))">导出逐桶评估 CSV</button>
            </div>
          </section>

          <section class="panel">
            <div class="section-top"><div><p class="eyebrow">01 / OBSERVE</p><h2>接收新观测</h2></div><button v-if="session.mode === 'replay' && !session.restored" class="secondary" :disabled="busy || session.status !== 'active' || latest.cutoff >= (session.replay_end || session.versions[0].prediction.at(-1).timestamp)" @click="run(releaseNext)">释放下一桶历史观测</button></div>
            <p class="hint">先用接收前已发出的预测评估新数据，再生成下一版。批量提交只在批次末更新一次；重复或冲突操作会被拦截。</p>
            <form @submit.prevent="run(submitObservation)">
              <div class="field-grid"><label>下一个桶结束时间<input v-model="observation.timestamp" required /></label><label>真实发帖 / 评论数量<input v-model.number="observation.volume" type="number" min="0" max="1000000000000" step="any" required /></label></div>
              <label>来源或本次采集说明<input v-model="sourceNote" maxlength="2000" placeholder="采集平台、查询条件、导出批次等" /></label>
              <label>本次新增的已知背景（可选）<textarea v-model="contextText" rows="3" maxlength="5000" placeholder="已经发生的新事件及可靠来源。生成下一轮推演材料时会一并保留。" /></label>
              <button :disabled="busy || session.status !== 'active'">接收观测并自动更新</button>
              <label>批量新增观测 CSV / JSON<input type="file" accept=".csv,.json" :disabled="busy || session.status !== 'active'" @change="uploadObservations" /></label>
            </form>
          </section>

          <section class="panel">
            <p class="eyebrow">02 / CALIBRATE</p><h2>校准决策与结果</h2>
            <div class="notice" :class="{ success: latest.calibration.active }">{{ latest.calibration.reason }}</div>
            <div class="metric-grid">
              <div><span>已验证观测</span><strong>{{ session.summary.scored_buckets }} <small>桶</small></strong></div>
              <div><span>当前方案 MAE</span><strong>{{ numeric(session.summary.metrics.predicted?.mae) }}</strong></div>
              <div><span>未校准曲线 MAE</span><strong>{{ numeric(session.summary.metrics.base?.mae) }}</strong></div>
              <div><span>相对未校准改进</span><strong>{{ percentage(session.summary.mae_improvement_vs_base) }}</strong></div>
            </div>
            <p class="hint">校准样本 {{ latest.calibration.sample_count }} · 增益 {{ numeric(latest.calibration.gain) }} · 偏置 {{ numeric(latest.calibration.bias) }} · 已启用 {{ session.summary.calibrated_versions }} 版 · 追加校准 API 请求 0 次</p>
            <p v-if="session.usage" class="hint">关联模型账本：{{ session.usage.totals.requests }} 次请求，输入 / 输出 Token {{ session.usage.totals.input_tokens }} / {{ session.usage.totals.output_tokens }}，估算费用 {{ session.usage.totals.estimated_cny == null ? '无法完整估算' : `¥${session.usage.totals.estimated_cny.toFixed(4)}` }}。含关联准备和报告，Zep 另计。</p>
            <p v-if="session.summary.unscored_buckets" class="notice">{{ session.summary.unscored_buckets }} 桶没有事先发出的预测，已排除评分。</p>
            <p class="hint">{{ session.summary.note }} 当前 {{ session.summary.prospective_buckets }} 桶在系统发出预测之后结束；历史回放不会标成实时预测。</p>
            <div class="table-scroll"><table><caption>相同观测条件下的对照</caption><thead><tr><th>方法</th><th>MAE</th><th>RMSE</th><th>WAPE</th></tr></thead><tbody><tr v-for="(metrics, key) in session.summary.metrics" :key="key"><td>{{ metricNames[key] }}</td><td>{{ numeric(metrics.mae) }}</td><td>{{ numeric(metrics.rmse) }}</td><td>{{ percentage(metrics.wape) }}</td></tr></tbody></table></div>
          </section>

          <section class="panel">
            <div class="section-top"><div><p class="eyebrow">03 / FORECAST</p><h2>预测版本与轨迹</h2></div><label class="compact">查看版本<select v-model.number="versionIndex"><option v-for="version in session.versions" :key="version.number" :value="version.number">v{{ version.number }} · {{ displayTime(version.cutoff) }}</option></select></label></div>
            <p class="hint">所选版本仅使用截至 {{ displayTime(selectedVersion.cutoff) }} 的 {{ selectedVersion.observed_buckets }} 桶观测。{{ selectedVersion.reason }}</p>
            <figure class="chart"><figcaption><span class="legend actual">真实观测</span><span class="legend base">未校准</span><span class="legend predicted">该版预测</span></figcaption>
              <svg viewBox="0 0 760 260" role="img" aria-label="真实观测与所选版本预测曲线">
                <line x1="45" y1="215" x2="735" y2="215" stroke="#dedbd5" />
                <text x="5" y="25" font-size="11" fill="#737b83">{{ numeric(chartMaximum) }}</text><text x="25" y="219" font-size="11" fill="#737b83">0</text>
                <polyline :points="points(session.observations)" fill="none" stroke="#26313c" stroke-width="2.5" />
                <polyline :points="points(selectedVersion.base)" fill="none" stroke="#a5adb5" stroke-width="2" stroke-dasharray="6 5" />
                <polyline :points="points(selectedVersion.prediction)" fill="none" stroke="#eb652f" stroke-width="3" />
                <text x="45" y="244" font-size="11" fill="#737b83">{{ displayTime(chartStart) }}</text><text x="540" y="244" font-size="11" fill="#737b83">{{ displayTime(chartEnd) }}</text>
              </svg>
            </figure>
            <p v-if="selectedVersion.tail_extrapolated_buckets" class="notice">其中 {{ selectedVersion.tail_extrapolated_buckets }} 个参考桶超过原推演覆盖范围，使用尾值持平外推。可生成新推演替换参考曲线。</p>
            <div class="table-scroll"><table><thead><tr><th>未来桶结束</th><th>未校准量</th><th>预测量</th></tr></thead><tbody><tr v-for="(point, index) in selectedVersion.prediction" :key="point.timestamp"><td>{{ displayTime(point.timestamp) }}</td><td>{{ numeric(selectedVersion.base[index].volume) }}</td><td>{{ numeric(point.volume) }}</td></tr></tbody></table></div>
            <p class="fingerprint">版本指纹 {{ selectedVersion.snapshot_hash }}</p>
            <button class="secondary" :disabled="busy" @click="run(publishVersion)">将此版本冻结到预测验证模块</button>
            <div v-if="publishedCase" class="notice success"><RouterLink :to="`/research?caseId=${publishedCase.case_id}`">进入已冻结 v{{ publishedCase.version }} 的预测验证与导出页面 ↗</RouterLink></div>
          </section>

          <section class="panel">
            <p class="eyebrow">04 / REFRESH</p><h2>需要新的多智能体推演时</h2>
            <p v-if="session.summary.refresh_recommended" class="notice">持续偏差尚未通过本地校准门槛，建议核查数据、背景变化，并考虑重新推演。</p>
            <p class="hint">本地校准调整数值热度，不会推断新发生的语义事件。可将已接收观测生成新的早期材料案例，建立独立项目与图谱后推演，再接入新的冻结预测。此处生成材料不调用模型。</p>
            <button :disabled="busy" @click="run(createNextCase)">生成本轮已知材料案例</button>
            <div v-if="nextCase" class="notice success">已生成 {{ nextCase.case.case_id }}。<RouterLink :to="`/research?caseId=${nextCase.case.case_id}`">前往创建新推演 ↗</RouterLink></div>
            <form @submit.prevent="run(rebase)"><label>新一轮已冻结的预测 ID<input v-model="rebaseId" placeholder="forecast_…" required /></label><button class="secondary" :disabled="busy || session.status !== 'active'">接入新参考预测</button></form>
            <p class="hint">系统校验新预测采用与本轮完全相同的已知观测和时间窗口，接入后重新积累校准误差；旧版本与评分继续保留。</p>
          </section>
        </div>
        <section v-else class="panel empty"><p class="eyebrow">START WITH AN ISSUED FORECAST</p><h2>先选一个滚动任务。</h2><p>支持接收真实事件数据，也可以用合成示例逐桶检查闭环。</p></section>
      </section>

      <section v-if="activeTab === 'intake'" class="two-columns">
        <form class="panel" @submit.prevent="run(aggregate)">
          <h2>原始帖子 → 连续时间桶</h2><p class="hint">原始字段：id、created_at，可选 platform、negative_label。按平台和帖子 ID 去重；时段为 [开始, 结束)，输出时间表示桶结束时刻。</p>
          <label>原始 CSV / JSON<input type="file" accept=".csv,.json" required @change="readPosts" /></label>
          <div class="field-grid"><label>采集开始时间<input v-model="intake.start" required placeholder="2026-01-01T00:00:00+08:00" /></label><label>采集结束时间<input v-model="intake.end" required placeholder="2026-01-02T00:00:00+08:00" /></label></div>
          <label>每桶小时数<input v-model.number="intake.bucket_hours" type="number" min="1" max="168" required /></label>
          <label>平台<input v-model="intake.source.platform" required placeholder="与后续数据保持一致" /></label>
          <label>来源名称<input v-model="intake.source.source_name" required /></label>
          <label>来源链接（可选）<input v-model="intake.source.source_url" type="url" /></label>
          <label>采样口径<textarea v-model="intake.source.sampling_method" rows="3" required placeholder="关键词、帖子与评论范围、接口/导出方式及已知遗漏等" /></label>
          <label>仿真日内作息时区（UTC 偏移小时）<input v-model.number="intake.source.timezone_offset_hours" type="number" min="-12" max="14" required /></label>
          <label class="checkbox-label"><input v-model="intake.coverage_complete" type="checkbox" required />我确认所选时段采集完整，允许将空桶计零</label>
          <button :disabled="busy || !intake.content">清洗并聚合</button>
          <button type="button" class="secondary" @click="downloadPostTemplate">下载原始帖子模板</button>
        </form>
        <section class="panel"><h2>导入检查结果</h2><template v-if="aggregated"><p>原始 {{ aggregated.audit.input_records }} 条，去重 {{ aggregated.audit.duplicates_removed }} 条，窗口外 {{ aggregated.audit.outside_window }} 条。</p><p class="hint">{{ aggregated.audit.note }}</p><button @click="downloadAggregated">下载标准时间桶 JSON</button><form @submit.prevent="run(createAggregatedCase)"><label>事件名称<input v-model="aggregatedName" required /></label><label>预测截止时间<input v-model="aggregatedCutoff" required placeholder="对应一个已完整观测的桶结束时刻" /></label><label>预测未来桶数<input v-model.number="aggregatedHorizon" type="number" min="1" max="500" required /></label><button :disabled="busy">创建带来源记录的验证案例</button></form></template><p v-else class="hint">数据采集不完整时，请先补齐采集或改用连续的可靠时段。系统不会将缺失样本伪装成真实零值。</p></section>
      </section>

      <section v-if="activeTab === 'readiness'" class="panel">
        <div class="section-top"><div><h2>真实事件运行检查</h2><p class="hint">配置检查不会调用模型，也不会验证 API Key 有效性。</p></div><button :disabled="busy" @click="run(checkReadiness)">重新检查</button></div>
        <label>检查案例<select v-model="readinessCase" @change="run(checkReadiness)"><option value="">仅检查环境</option><option v-for="item in cases" :key="item.case_id" :value="item.case_id">{{ item.name }}</option></select></label>
        <div v-if="readiness" class="readiness-list"><div v-for="check in readiness.checks" :key="check.id"><span :class="check.passed ? 'pass' : 'pending'">{{ check.passed ? '已具备' : '待补齐' }}</span><strong>{{ check.label }}</strong></div><p class="hint">{{ readiness.note }}</p></div>
        <ol class="workflow-list"><li>准备同平台、同口径的真实帖子 / 评论数量和截止前背景，记录来源。</li><li>在验证模块创建早期案例，运行原有推演并冻结初始预测。</li><li>创建实际接收任务，逐次提交新观测；自动评分、检测偏差并按门槛校准。</li><li>需要新语义推演时生成本轮材料，使用独立图谱运行后接回滚动任务。</li><li>导出逐桶评估和完整备份，比较误差、覆盖范围、Token、费用与耗时。</li></ol>
        <p class="notice">真实预测能力需要独立真实事件的回测与前瞻验证。软件检查通过不等于已经达到某个准确率或可靠性百分比。</p>
      </section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as api from '../api/research'

const route = useRoute(), router = useRouter()
const tabs = [{ id: 'workspace', label: '滚动预测' }, { id: 'intake', label: '真实数据准备' }, { id: 'readiness', label: '运行检查' }]
const activeTab = ref('workspace'), busy = ref(false), error = ref(''), message = ref('')
const cases = ref([]), forecasts = ref([]), sessions = ref([]), session = ref(null), versionIndex = ref(0)
const form = ref({ case_id: route.query.caseId || '', forecast_id: route.query.forecastId || '', mode: 'live', policy: { window: 6, trigger_ratio: .35, consecutive: 2 } })
const observation = ref({ timestamp: '', volume: '' }), sourceNote = ref(''), contextText = ref(''), nextCase = ref(null), rebaseId = ref('')
const readiness = ref(null), readinessCase = ref('')
const intake = ref({ content: '', format: 'csv', start: '', end: '', bucket_hours: 1, coverage_complete: false, source: { platform: '', source_name: '', source_url: '', sampling_method: '', timezone_offset_hours: 8 } })
const aggregated = ref(null), aggregatedName = ref(''), aggregatedCutoff = ref(''), aggregatedHorizon = ref(6)
const publishedCase = ref(null)
const metricNames = { predicted: '实际采用的滚动方案', base: '未校准参考曲线', candidate: '影子校准候选', last_value: '同信息：保持最近值', moving_average: '同信息：近 3 桶均值' }
const latest = computed(() => session.value?.versions.at(-1))
const selectedVersion = computed(() => session.value?.versions[versionIndex.value] || latest.value)
const chartRows = computed(() => session.value ? [...session.value.observations, ...selectedVersion.value.base, ...selectedVersion.value.prediction] : [])
const chartMaximum = computed(() => Math.max(1, ...chartRows.value.map(point => point.volume)))
const chartStart = computed(() => chartRows.value.length ? new Date(chartRows.value[0].timestamp).toISOString() : '')
const chartEnd = computed(() => chartRows.value.length ? new Date(Math.max(...chartRows.value.map(row => +new Date(row.timestamp)))).toISOString() : '')
const numeric = value => value == null || !Number.isFinite(value) ? '—' : Number(value.toFixed(3)).toLocaleString()
const percentage = value => value == null ? '—' : `${numeric(value * 100)}%`
const displayTime = value => value ? new Date(value).toLocaleString() : '—'

async function run(task) {
  if (busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try { await task() } catch (cause) { error.value = cause.message || '操作失败' } finally { busy.value = false }
}
function mutation(extra = {}) { return { expected_revision: session.value.revision, request_id: crypto.randomUUID(), ...extra } }
async function refreshLists() {
  const results = await Promise.all([api.listCases(), api.listRolling()])
  cases.value = results[0].data; sessions.value = results[1].data
}
function setSession(value) {
  session.value = value; versionIndex.value = value.versions.length - 1; nextCase.value = null; publishedCase.value = null
  observation.value = { timestamp: new Date(+new Date(latest.value.cutoff) + value.bucket_hours * 3600000).toISOString(), volume: '' }
}
async function selectSession(id) {
  setSession((await api.getRolling(id)).data)
  await router.replace({ query: { sessionId: id } })
}
async function loadForecasts() {
  forecasts.value = form.value.case_id ? (await api.listForecasts(form.value.case_id)).data : []
  if (!forecasts.value.some(row => row.forecast_id === form.value.forecast_id)) form.value.forecast_id = ''
}
async function createSession() {
  const value = (await api.createRolling(form.value)).data
  await refreshLists(); await selectSession(value.session_id)
}
async function replaySession() {
  const value = (await api.replayRolling(form.value)).data
  await refreshLists(); await selectSession(value.session_id)
  message.value = '历史数据已逐桶释放并完成回放。结果仅用于回测，不标记为前瞻预测。'
}
async function createDemo() {
  const value = (await api.createRollingDemo()).data
  await refreshLists(); await selectSession(value.session_id); activeTab.value = 'workspace'
  message.value = '合成示例已创建。逐次释放观测，查看校准是否触发。'
}
async function updateFrom(response) {
  setSession(response.data); await refreshLists()
  message.value = '新观测已接收：旧预测评分完成，下一版本已冻结。'
}
async function submitObservation() {
  await updateFrom(await api.ingestObservations(session.value.session_id, mutation({ rows: [observation.value], source_note: sourceNote.value, context_text: contextText.value })))
  contextText.value = ''
}
async function releaseNext() {
  await updateFrom(await api.replayRollingStep(session.value.session_id, mutation({ timestamp: observation.value.timestamp })))
}
async function readFile(event) {
  const file = event.target.files?.[0]
  if (!file) return null
  if (file.size > 10 * 1024 * 1024) throw new Error('文件不能超过 10 MB')
  return { content: await file.text(), format: file.name.toLowerCase().endsWith('.json') ? 'json' : 'csv' }
}
async function uploadObservations(event) {
  await run(async () => { const data = await readFile(event); if (data) { await updateFrom(await api.ingestObservations(session.value.session_id, mutation({ ...data, source_note: sourceNote.value, context_text: contextText.value }))); contextText.value = '' } })
  event.target.value = ''
}
async function toggleStatus() {
  setSession((await api.setRollingStatus(session.value.session_id, mutation({ status: session.value.status === 'active' ? 'paused' : 'active' }))).data)
  await refreshLists()
}
function download(blob, name) {
  const link = document.createElement('a'), url = URL.createObjectURL(blob)
  link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function exportData(format) { download(await api.exportRolling(session.value.session_id, format), `${session.value.session_id}.${format}`) }
async function restoreFile(event) {
  await run(async () => {
    const data = await readFile(event); if (!data) return
    const value = (await api.restoreRolling(JSON.parse(data.content))).data
    await refreshLists(); await selectSession(value.session_id)
    message.value = '备份已恢复为暂停的历史回放任务，请核对来源后恢复接收。'
  })
  event.target.value = ''
}
async function createNextCase() { nextCase.value = (await api.nextRollingCase(session.value.session_id, { expected_revision: session.value.revision })).data; await refreshLists() }
async function rebase() {
  setSession((await api.rebaseRolling(session.value.session_id, mutation({ forecast_id: rebaseId.value.trim() }))).data)
  await refreshLists(); message.value = '新参考预测已接入，旧预测和已评分记录继续保留。'
}
async function publishVersion() {
  const selected = versionIndex.value
  const value = (await api.publishRolling(session.value.session_id, mutation({ version: versionIndex.value }))).data
  await selectSession(session.value.session_id); await refreshLists()
  versionIndex.value = selected
  publishedCase.value = value
  message.value = `已冻结为 ${value.forecast_id}，可在预测验证模块选择对应案例评估。`
}
function points(rows) {
  const begin = +new Date(chartStart.value), span = Math.max(+new Date(chartEnd.value) - begin, 1)
  return rows.map(row => `${45 + (+new Date(row.timestamp) - begin) / span * 690},${215 - row.volume / chartMaximum.value * 190}`).join(' ')
}
async function readPosts(event) { await run(async () => { const data = await readFile(event); if (data) Object.assign(intake.value, data) }) }
async function aggregate() { aggregated.value = (await api.aggregatePosts(intake.value)).data }
function downloadPostTemplate() {
  download(new Blob(['id,created_at,platform,negative_label\npost-1,2026-01-01T00:10:00+08:00,example,1\npost-2,2026-01-01T01:20:00+08:00,example,0\n'], { type: 'text/csv;charset=utf-8' }), 'raw-posts-template.csv')
}
function downloadAggregated() { download(new Blob([JSON.stringify(aggregated.value.rows, null, 2)], { type: 'application/json' }), 'aggregated-observations.json') }
async function createAggregatedCase() {
  const value = (await api.createCase({ name: aggregatedName.value, rows: aggregated.value.rows, cutoff: aggregatedCutoff.value, bucket_hours: intake.value.bucket_hours, horizon: aggregatedHorizon.value, source: aggregated.value.source })).data
  await refreshLists(); message.value = `已创建 ${value.case_id}，可进入预测验证模块生成初始预测。`
}
async function checkReadiness() { readiness.value = (await api.getReadiness(readinessCase.value)).data }
onMounted(() => run(async () => {
  await refreshLists(); await checkReadiness()
  if (form.value.case_id) await loadForecasts()
  if (route.query.sessionId) await selectSession(route.query.sessionId)
  else if (sessions.value.length) await selectSession(sessions.value[0].session_id)
}))
</script>

<style scoped>
*{box-sizing:border-box}.rolling-page{min-height:100vh;background:#f4f3ef;color:#242b33;font-family:'Noto Sans SC',system-ui,sans-serif}.topbar{min-height:68px;padding:18px 5%;display:flex;align-items:center;justify-content:space-between;gap:20px;background:#20262e;color:white}.topbar a{color:inherit;text-decoration:none;font-size:13px}.brand{font-size:17px!important;font-weight:800;letter-spacing:1px}.brand span{font-size:11px;font-weight:400;color:#aab7c5}main{max-width:1480px;margin:auto;padding:36px 32px 80px}.intro{display:flex;justify-content:space-between;align-items:center;gap:24px}.eyebrow{font-size:11px;color:#8a8077;letter-spacing:1px;overflow-wrap:anywhere;margin:0 0 10px}h1{font-size:34px;margin:0 0 10px}h2{font-size:18px;margin:0 0 16px}.intro p:last-child{font-size:14px;color:#737b83}button{font:inherit;font-size:13px;font-weight:600;padding:11px 15px;border:1px solid transparent;border-radius:5px;background:#e86430;color:white;cursor:pointer}button:hover{background:#d45120}button:disabled{opacity:.45;cursor:not-allowed}.secondary{border-color:#dcd9d2;background:white;color:#3c454e}.secondary:hover{background:#f4f3ef}.full{width:100%}.tabs{display:flex;gap:8px;margin:26px 0}.tabs button{background:transparent;color:#737b83}.tabs button.selected{background:#26313c;color:white}.workspace{display:grid;grid-template-columns:285px minmax(0,1fr);gap:22px;align-items:start}.panel{background:white;border:1px solid #e1ded6;border-radius:9px;padding:25px;min-width:0}.content{display:grid;gap:20px}.sidebar{padding:22px 18px}.session-row{display:flex;flex-direction:column;text-align:left;width:100%;gap:8px;background:#f6f5f1;color:inherit;margin-bottom:10px;overflow-wrap:anywhere}.session-row span{font-size:11px;color:#7a8188;font-weight:400}.session-row:hover,.session-row.chosen{border-color:#e86430;background:#fff4ec}.hint{font-size:12px;color:#737b83;line-height:1.85}.text-link{display:block;margin:20px 0;font-size:12px;color:#ca511e}.notice{padding:12px 15px;border:1px solid #ecdbc4;border-radius:5px;background:#fff5e8;font-size:12px;line-height:1.8;margin:14px 0;overflow-wrap:anywhere}.notice.error{color:#9f332b;background:#fff0ed;border-color:#edc2b9}.notice.success{color:#316346;background:#eff8f0;border-color:#c6e0c9}.notice a{color:inherit}.section-top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.badge{font-size:11px;color:#65716c;border:1px solid #deddd7;padding:7px 9px;white-space:nowrap;border-radius:4px}.button-row{display:flex;flex-wrap:wrap;gap:9px}.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}label{display:block;font-size:12px;color:#636d77;margin:14px 0;min-width:0}input:not([type=checkbox]),select,textarea{display:block;width:100%;padding:10px 11px;margin-top:7px;font:inherit;font-size:13px;color:#26313c;border:1px solid #d9dde0;border-radius:5px;background:white}input[type=file]{font-size:11px!important;padding:8px!important}input:focus,select:focus,textarea:focus{outline:2px solid #f2b79f;outline-offset:1px}input[type=checkbox]{accent-color:#e86430}.checkbox-label{display:flex;gap:9px;align-items:center;line-height:1.7}details{margin:20px 0;border-top:1px solid #eeeae3;padding-top:15px}summary{font-size:13px;font-weight:600;cursor:pointer}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin:22px 0}.metric-grid>div{background:#f7f6f2;border:1px solid #efede7;border-radius:5px;padding:15px;min-width:0}.metric-grid span{display:block;font-size:11px;color:#7b838b;margin-bottom:10px}.metric-grid strong{font-size:25px;overflow-wrap:anywhere}.metric-grid small{font-size:11px;font-weight:400}.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:12px}caption{text-align:left;font-size:13px;font-weight:600;margin:16px 0}th{background:#f7f6f2;color:#7b838b;font-weight:500}th,td{padding:12px;border-bottom:1px solid #e9e6df;overflow-wrap:anywhere}.compact{margin:0;min-width:180px}.chart{margin:25px 0}.chart svg{width:100%;max-height:300px}.chart figcaption{display:flex;gap:16px;font-size:11px;color:#7a828b}.legend:before{content:'';display:inline-block;width:17px;height:3px;margin:0 6px 3px 0;background:#26313c}.legend.base:before{background:#a5adb5}.legend.predicted:before{background:#eb652f}.fingerprint{font:10px monospace;color:#999;overflow-wrap:anywhere;margin:20px 0}.empty{min-height:340px;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center}.empty>p:last-child{font-size:13px;color:#7a828b}.two-columns{display:grid;grid-template-columns:1fr 1fr;gap:22px;align-items:start}.readiness-list>div{display:flex;align-items:center;gap:18px;border-bottom:1px solid #eeeae3;padding:18px 0;font-size:13px}.readiness-list span{font-size:11px;padding:6px 9px;border-radius:4px}.pass{background:#edf7ef;color:#3e7551}.pending{background:#fff2df;color:#996b29}.workflow-list{font-size:13px;line-height:2.2;color:#65707b;padding-left:22px}.restore{margin-top:25px}@media(max-width:1080px){.workspace{grid-template-columns:245px minmax(0,1fr)}.metric-grid{grid-template-columns:1fr 1fr}.section-top{flex-wrap:wrap}}@media(max-width:720px){main{padding:26px 18px 60px}.workspace,.two-columns{grid-template-columns:1fr}.intro{align-items:flex-start;flex-direction:column}.topbar{align-items:flex-start;flex-direction:column;gap:10px}.panel{padding:20px}h1{font-size:29px}.field-grid{grid-template-columns:1fr}.tabs{gap:3px}.tabs button{padding:10px 12px}.metric-grid strong{font-size:23px}.chart figcaption{gap:10px}.compact{width:100%}}
</style>
