<template>
  <div class="research-page">
    <header class="topbar"><RouterLink to="/" class="brand">MIROFISH <span>/ RESEARCH</span></RouterLink><span class="local-label">本地研究工作台</span></header>
    <main>
      <div class="intro"><div><p class="eyebrow">验证预测 · 记录成本 · 对照实验</p><h1>让每次推演有据可查。</h1><p>先固定预测，再比较真实后续数据。用量、缓存和运行时间同时留痕。</p></div><button :disabled="busy" @click="run(demo)">用合成示例跑通验证 ↗</button></div>
      <div v-if="policy.research_only" class="notice">离线验证模式：无需 API Key，可导入、评分和查看账本；模型与云图谱操作已关闭。</div>
      <div v-if="error" class="notice error" role="alert">{{ error }}</div><div v-if="message" class="notice success" role="status">{{ message }}</div>
      <nav class="tabs" aria-label="工作台功能"><button v-for="tab in tabs" :key="tab.id" :class="{ selected: activeTab === tab.id }" :aria-pressed="activeTab === tab.id" @click="activeTab = tab.id">{{ tab.label }}</button><RouterLink class="text-button" to="/research/rolling">滚动预测与自动纠偏 ↗</RouterLink></nav>

      <section v-if="activeTab === 'validation'" class="validation-layout">
        <aside class="panel cases-panel"><h2>验证案例</h2><p class="hint">每个案例固定截止时间与预测窗口。</p><p v-if="!cases.length" class="empty">暂无案例。可以先运行合成示例。</p>
          <button v-for="item in cases" :key="item.case_id" class="case-row" :class="{ chosen: caseId === item.case_id }" @click="run(() => selectCase(item.case_id))"><strong>{{ item.name }}</strong><span>{{ item.is_demo ? '合成示例' : '用户提供数据' }} · 未来 {{ item.horizon }} 桶</span></button>
          <details :open="!cases.length"><summary>导入新案例</summary><form @submit.prevent="run(importCase)">
            <label>案例名称<input v-model="caseForm.name" required maxlength="120" placeholder="例如：某事件首日舆情" /></label>
            <label>观测数据 CSV / JSON<input type="file" accept=".csv,.json" required @change="readCaseFile" /></label>
            <p class="hint">字段：timestamp、volume，可选 negative_ratio。时间为桶结束时刻，须带时区；数据须按固定桶长连续聚合。</p>
            <label>预测截止时间<input v-model="caseForm.cutoff" required placeholder="2026-01-01T11:00:00+08:00" /></label>
            <div class="field-grid"><label>每桶小时<input v-model.number="caseForm.bucket_hours" type="number" min="1" max="168" required /></label><label>预测未来桶数<input v-model.number="caseForm.horizon" type="number" min="1" max="500" required /></label></div>
            <label>截止前背景材料（可选）<textarea v-model="caseForm.background" rows="4" placeholder="参与主体、已发生事件。请勿加入截止时间之后的信息。" /></label>
            <details><summary>数据来源与采样口径</summary><label>平台<input v-model="caseForm.source.platform" placeholder="同一平台，后续保持一致" /></label><label>来源名称<input v-model="caseForm.source.source_name" /></label><label>来源链接<input v-model="caseForm.source.source_url" type="url" /></label><label>采样口径<textarea v-model="caseForm.source.sampling_method" rows="3" placeholder="关键词、采集范围、帖子/评论范围、已知遗漏" /></label><label>仿真作息时区（UTC 偏移小时）<input v-model.number="caseForm.source.timezone_offset_hours" type="number" min="-12" max="14" /></label></details>
            <button class="full" :disabled="busy || !caseForm.content">创建验证案例</button>
          </form></details>
          <button class="text-button" @click="downloadTemplate">下载数据模板</button>
        </aside>

        <div v-if="selectedCase" class="case-content">
          <section class="panel"><div class="section-top"><div><p class="eyebrow">CASE / {{ selectedCase.case_id }}</p><h2>{{ selectedCase.name }}</h2></div><span class="badge" :class="{ demo: selectedCase.is_demo }">{{ selectedCase.is_demo ? '合成数据 · 不代表真实准确率' : '用户数据 · 来源需核验' }}</span></div>
            <div class="case-facts"><span>截止 <strong>{{ displayTime(selectedCase.cutoff) }}</strong></span><span>早期观测 <strong>{{ selectedCase.train.length }} 桶</strong></span><span>后续真实值 <strong>{{ selectedCase.truth_buckets }}/{{ selectedCase.horizon }} 桶</strong></span></div>
            <p class="hint">后续真实数据不会进入系统导出的早期输入。计数口径必须是同一平台、同一采样方式下的发帖与评论数量。</p>
            <div class="button-row"><button class="secondary" :disabled="busy" @click="run(exportSeed)">导出仅含早期数据的材料</button><button class="secondary" :disabled="busy || policy.research_only" @click="run(createProject)">用早期材料创建推演（调用模型）</button></div>
            <label class="inline-upload">补充真实后续数据<input type="file" accept=".csv,.json" @change="uploadTruth" /></label>
          </section>

          <section class="panel"><h2>1 / 冻结预测</h2><p class="hint">冻结后不可修改。更改方法或参数需要保存一个新的预测快照。</p>
            <form @submit.prevent="run(freeze)"><div class="field-grid"><label>预测来源<select v-model="forecastForm.method"><option value="simulation">已有 MiroFish 仿真</option><option value="import">导入外部预测</option><option value="moving_average">基线：近 3 桶均值</option><option value="last_value">基线：保持最近值</option></select></label><label>实验标签<input v-model="forecastForm.variant" maxlength="60" placeholder="原版 / 优化版 / 基线" /></label></div>
              <template v-if="forecastForm.method === 'simulation'"><label>仿真实例 ID<input v-model="forecastForm.simulation_id" required placeholder="sim_…，须已完成运行" /></label><div class="field-grid"><label>平台<select v-model="forecastForm.platform"><option value="reddit">Reddit / Community</option><option value="twitter">Twitter / Plaza</option></select></label><label>热度映射<select v-model="forecastForm.scale_mode"><option value="history_anchor">用截止前最后一桶锚定规模</option><option value="raw">保留原始发帖 / 评论计数</option></select></label></div><p class="hint">锚定只读取早期观测，不使用后续真实值；其预测的是相对热度形状，绝对传播规模仍需校准。缺少足够仿真轮次时不能评分。</p></template>
              <label v-if="forecastForm.method === 'import'">预测 CSV / JSON<input type="file" accept=".csv,.json" required @change="readForecastFile" /></label>
              <button :disabled="busy">冻结预测快照</button>
            </form>
          </section>

          <section class="panel"><div class="section-top"><h2>2 / 与真实数据对比</h2><button class="secondary" :disabled="busy || selectedForecasts.length < 2" @click="run(compare)">对比勾选的预测</button></div>
            <p class="hint">冻结初始预测后，可前往 <RouterLink :to="`/research/rolling?caseId=${caseId}`">滚动预测工作台</RouterLink>，接收新观测并自动纠偏。</p>
            <p v-if="!forecasts.length" class="empty">先冻结一个预测，再进行评估。</p>
            <div v-for="item in forecasts" :key="item.forecast_id" class="forecast-row"><input v-model="selectedForecasts" type="checkbox" :value="item.forecast_id" :aria-label="`选择 ${item.variant || item.method}`" /><div><strong>{{ item.variant || item.method }}</strong><span>{{ methodLabel(item.method) }} · {{ item.forecast_id }}</span></div><button class="secondary" :disabled="busy" @click="run(() => evaluate(item.forecast_id))">计算误差</button></div>
          </section>

          <section v-if="evaluation" class="panel results"><div class="section-top"><h2>评估结果</h2><div class="button-row"><button class="secondary" @click="run(() => exportResult('json'))">导出 JSON</button><button class="secondary" @click="run(() => exportResult('csv'))">导出 CSV</button></div></div>
            <div v-if="evaluation.forecast.is_demo" class="notice">此结果仅用于验证软件流程，不能用来声称真实预测准确。</div>
            <p v-if="evaluation.forecast.metadata.warning" class="notice">{{ evaluation.forecast.metadata.warning }}</p>
            <div class="metric-grid"><div><span>MAE · 越低越好</span><strong>{{ numeric(evaluation.scores.volume.mae) }}</strong></div><div><span>RMSE · 越低越好</span><strong>{{ numeric(evaluation.scores.volume.rmse) }}</strong></div><div><span>WAPE · 总量加权误差</span><strong>{{ percentage(evaluation.scores.volume.wape) }}</strong></div><div><span>峰值时间差</span><strong>{{ numeric(evaluation.scores.volume.peak_time_error_hours) }} <small>小时</small></strong></div></div>
            <figure class="curve"><figcaption><span class="legend real">真实后续值</span><span class="legend predicted">冻结预测</span></figcaption><svg viewBox="0 0 720 240" role="img" aria-label="冻结预测与真实后续热度对比曲线"><path d="M35 15 V210 H705" fill="none" stroke="#d9d9d9" /><polyline :points="chartPoints(evaluation.scores.truth)" fill="none" stroke="#20252c" stroke-width="3" /><polyline :points="chartPoints(evaluation.scores.prediction)" fill="none" stroke="#ed632c" stroke-width="3" /><text x="38" y="232" font-size="12">预测窗口开始</text><text x="620" y="232" font-size="12">预测窗口结束</text></svg></figure>
            <table><caption>与简单方法对比</caption><thead><tr><th>方法</th><th>MAE</th><th>当前预测相对该基线的改进</th></tr></thead><tbody><tr v-for="(item, key) in evaluation.scores.baselines" :key="key"><td>{{ methodLabel(key) }}</td><td>{{ numeric(item.metrics.mae) }}</td><td>{{ percentage(item.metrics.mae_skill) }}</td></tr></tbody></table>
            <p class="hint">改进为正表示误差较基线更低；负值表示更差。没有使用“100%－误差”冒充准确率。负面比例匹配 {{ evaluation.scores.negative_ratio.matched_buckets }}/{{ evaluation.scores.negative_ratio.total_buckets }} 桶，MAE：{{ numeric(evaluation.scores.negative_ratio.mae) }}。</p>
            <p class="fingerprint">预测指纹 {{ evaluation.forecast.snapshot_hash }}</p>
          </section>

          <section v-if="comparison" class="panel"><h2>同案例实验对比</h2><div class="table-scroll"><table><thead><tr><th>实验</th><th>MAE</th><th>WAPE</th><th>SDK 请求</th><th>Token 输入 / 输出</th><th>估算模型费用</th><th>请求时间窗</th></tr></thead><tbody><tr v-for="item in comparison.rows" :key="item.forecast_id"><td>{{ item.variant || item.method }}</td><td>{{ numeric(item.metrics.mae) }}</td><td>{{ percentage(item.metrics.wape) }}</td><td>{{ item.usage?.requests ?? '—' }}</td><td>{{ item.usage ? `${item.usage.input_tokens} / ${item.usage.output_tokens}` : '—' }}</td><td>{{ currency(item.usage?.estimated_cny) }}</td><td>{{ item.usage ? `${numeric(item.usage.wall_time_ms / 1000)} 秒` : '—' }}</td></tr></tbody></table></div><p class="hint">{{ comparison.note }} 请求时间窗不含全部图谱与排队耗时，不等同完整任务总耗时。</p></section>
        </div><div v-else class="panel empty-state"><p class="eyebrow">START WITH EVIDENCE</p><h2>先选择或导入一个案例。</h2><p>暂无真实数据也可以先用合成示例检查流程。</p></div>
      </section>

      <section v-if="activeTab === 'usage'" class="panel"><div class="section-top"><div><h2>API 用量与等待时间</h2><p class="hint">记录准备、仿真和报告阶段。金额按配置单价估算，Zep 费用另计。</p></div><button :disabled="busy" @click="run(refreshUsage)">刷新账本</button></div><label>按运行范围筛选（留空查看全部）<input v-model="scopeFilter" placeholder="sim_… / run_… / proj_…" /></label>
        <template v-if="usage"><div class="metric-grid"><div><span>SDK 请求</span><strong>{{ usage.totals.requests }}</strong></div><div><span>输入 / 输出 Token</span><strong class="token-value">{{ usage.totals.input_tokens.toLocaleString() }} / {{ usage.totals.output_tokens.toLocaleString() }}</strong></div><div><span>供应商缓存命中 Token</span><strong>{{ usage.totals.cached_tokens.toLocaleString() }}</strong></div><div><span>准备结果复用</span><strong>{{ usage.preparation_cache_hits }} <small>次</small></strong></div></div>
          <p class="cost-line">估算模型费用 <strong>{{ currency(usage.totals.estimated_cny) }}</strong><span v-if="usage.totals.incomplete_cost_requests"> · {{ usage.totals.incomplete_cost_requests }} 个请求尚不能完整计价，请检查单价或供应商用量返回。</span></p>
          <div class="table-scroll"><table><thead><tr><th>阶段</th><th>SDK 请求</th><th>输入 Token</th><th>输出 Token</th><th>平均请求耗时</th><th>请求时间窗</th><th>估算模型费</th></tr></thead><tbody><tr v-for="(item, stage) in usage.stages" :key="stage"><td>{{ stage }}</td><td>{{ item.requests }}</td><td>{{ item.input_tokens }}</td><td>{{ item.output_tokens }}</td><td>{{ numeric(item.total_request_latency_ms / Math.max(item.completed_requests, 1) / 1000) }} 秒</td><td>{{ numeric(item.wall_time_ms / 1000) }} 秒</td><td>{{ currency(item.estimated_cny) }}</td></tr></tbody></table></div>
          <p v-for="note in usage.notes" :key="note" class="hint">{{ note }}</p>
          <details><summary>最近请求明细</summary><div class="table-scroll"><table><thead><tr><th>范围</th><th>模型</th><th>模式</th><th>状态</th><th>耗时</th></tr></thead><tbody><tr v-for="item in usage.recent_requests" :key="item.id"><td>{{ item.scope_id }}</td><td>{{ item.model }}</td><td>{{ item.mode }}</td><td>{{ item.status }}</td><td>{{ numeric(item.duration_ms / 1000) }} 秒</td></tr></tbody></table></div></details>
        </template>
      </section>

      <section v-if="activeTab === 'settings'" class="settings-layout"><form class="panel" @submit.prevent="run(savePolicy)"><h2>省费与提速设置</h2><label>新任务默认模式<select v-model="policy.mode"><option value="efficient">优化模式：复用准备结果，并行配置生成</option><option value="baseline">对照模式：不复用准备结果，串行配置生成</option></select></label><p class="hint">两种模式保留相同角色提示词和决策模型。优化模式不缓存每轮角色决策；缓存仅复用同模型、同材料、同提示词的画像结果。</p>
        <p class="hint">需要独立重采样人设的实验，请选对照模式。固定初始化的成对实验可以复用同一画像。</p><label>配置生成并发数<input v-model.number="policy.prepare_workers" type="number" min="1" max="16" required /></label><label>每个运行范围的 SDK 请求上限<input v-model.number="policy.max_requests_per_scope" type="number" min="0" max="100000" required /></label><label>每个运行范围的金额阈值（元）<input v-model.number="policy.budget_cny_per_scope" type="number" min="0" step="0.01" required /></label><p class="hint">0 表示不限制。金额阈值需要配置单价；达到阈值后阻止新请求，并发中的请求仍可能产生费用。被预算中止的仿真不会标成完整预测。</p>
        <fieldset><legend>模型单价 · 元 / 百万 Token</legend><label>模型名称<input v-model="priceForm.model" placeholder="填写实际使用的模型名" /></label><div class="field-grid"><label>输入<input v-model.number="priceForm.input" type="number" min="0" step="0.001" /></label><label>输出<input v-model.number="priceForm.output" type="number" min="0" step="0.001" /></label><label>缓存命中输入（可选）<input v-model.number="priceForm.cached_input" type="number" min="0" step="0.001" /></label></div><p class="hint">按供应商当前账单填写，不预设过时价格。没有单价或用量时显示“无法完整估算”。</p></fieldset><button :disabled="busy">保存运行设置</button>
      </form><form class="panel" @submit.prevent="run(applyOptions)"><h2>固定仿真实验条件</h2><p class="hint">只可修改完成准备、尚未开始运行的实例。对比实验应使用相同材料、初始化、模型、轮次和随机种子。</p><label>仿真实例 ID<input v-model="optionsId" required placeholder="sim_…" /></label><label>本次模式<select v-model="options.mode"><option value="efficient">优化模式</option><option value="baseline">对照模式</option></select></label><label>角色激活随机种子<input v-model.number="options.random_seed" type="number" min="0" max="4294967295" required /></label><label>每平台模型最大并发<input v-model.number="options.llm_concurrency" type="number" min="1" max="128" required /></label><p class="hint">并发过高可能触发供应商限流。随机种子只固定角色抽样，不能保证云端模型输出逐字一致。</p><button :disabled="busy">保存到该仿真实例</button></form></section>
      <section v-if="activeTab === 'settings'" class="panel" style="margin-top:22px"><h2>启动前查看研究运行计划</h2><p class="hint">研究案例按预测窗口固定仿真时长，并对齐真实截止时刻的日内作息。模型请求和费用仍由角色激活、决策与重试决定。</p><label>仿真实例 ID<input v-model="optionsId" placeholder="sim_…" /></label><button class="secondary" :disabled="busy || !optionsId.trim()" @click="run(loadPlan)">查看运行计划（不调用模型）</button><div v-if="simulationPlan"><p class="hint">角色数 {{ simulationPlan.agent_count }} · 仿真 {{ simulationPlan.time_config.total_simulation_hours }} 小时 · 每轮 {{ simulationPlan.time_config.minutes_per_round }} 分钟 · 完整研究窗口需要 {{ simulationPlan.research_contract?.required_rounds ?? '未绑定研究案例' }} 轮</p><p class="hint">单范围 SDK 请求上限：{{ simulationPlan.max_requests_per_scope || '不限制' }} · 金额阈值：{{ simulationPlan.budget_cny_per_scope || '不限制' }}。{{ simulationPlan.note }}</p></div></section>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as api from '../api/research'
import { generateOntology } from '../api/graph'

const route = useRoute(), router = useRouter()
const tabs = [{ id: 'validation', label: '预测验证' }, { id: 'usage', label: '费用与速度' }, { id: 'settings', label: '运行设置' }]
const activeTab = ref('validation'), busy = ref(false), error = ref(''), message = ref('')
const cases = ref([]), caseId = ref(''), forecasts = ref([]), selectedForecasts = ref([])
const evaluation = ref(null), comparison = ref(null), usage = ref(null), scopeFilter = ref('')
const policy = ref({ mode: 'efficient', prepare_workers: 4, max_requests_per_scope: 0, budget_cny_per_scope: 0, prices: {}, research_only: true })
const caseForm = ref({ name: '', cutoff: '', bucket_hours: 1, horizon: 6, content: '', format: 'csv', background: '', source: { platform: '', source_name: '', source_url: '', sampling_method: '', timezone_offset_hours: 8 } })
const forecastForm = ref({ method: route.query.simulationId ? 'simulation' : 'moving_average', variant: '', simulation_id: route.query.simulationId || '', platform: 'reddit', scale_mode: 'history_anchor', content: '', format: 'csv' })
const priceForm = ref({ model: '', input: '', output: '', cached_input: '' })
const optionsId = ref(route.query.simulationId || ''), options = ref({ mode: 'efficient', random_seed: 42, llm_concurrency: 30 })
const simulationPlan = ref(null)
const selectedCase = computed(() => cases.value.find(item => item.case_id === caseId.value))
const numeric = value => value == null || !Number.isFinite(value) ? '—' : Number(value.toFixed(3)).toLocaleString()
const percentage = value => value == null ? '—' : `${numeric(value * 100)}%`
const currency = value => value == null ? '无法完整估算' : `¥${value.toFixed(4)}`
const displayTime = value => new Date(value).toLocaleString()
const methodLabel = value => ({ moving_average: '近 3 桶均值', last_value: '保持最近值', simulation: 'MiroFish 仿真', import: '外部预测', rolling: '滚动自适应预测' }[value] || value)

async function run(task) {
  if (busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try { await task() } catch (cause) { error.value = cause.message || '操作失败' } finally { busy.value = false }
}
async function reloadCases() { cases.value = (await api.listCases()).data }
async function selectCase(id) {
  caseId.value = id; evaluation.value = null; comparison.value = null; selectedForecasts.value = []
  forecasts.value = (await api.listForecasts(id)).data
}
async function demo() {
  const response = await api.createDemo()
  await reloadCases(); await selectCase(response.data.case_id)
  const frozen = await api.freezeForecast(caseId.value, { method: 'moving_average', variant: '合成示例 / 均值基线' })
  forecasts.value = (await api.listForecasts(caseId.value)).data
  await evaluate(frozen.data.forecast_id)
  activeTab.value = 'validation'
}
async function readCaseFile(event) {
  try {
    const file = event.target.files?.[0]; if (!file) return
    if (file.size > 10 * 1024 * 1024) throw new Error('数据文件不能超过 10 MB')
    caseForm.value.content = await file.text(); caseForm.value.format = file.name.endsWith('.json') ? 'json' : 'csv'
    if (!caseForm.value.name) caseForm.value.name = file.name.replace(/\.(csv|json)$/i, '')
  } catch (cause) { error.value = cause.message }
}
async function readForecastFile(event) {
  const file = event.target.files?.[0]; if (!file) return
  forecastForm.value.content = await file.text(); forecastForm.value.format = file.name.endsWith('.json') ? 'json' : 'csv'
}
async function importCase() {
  const response = await api.createCase(caseForm.value)
  await reloadCases(); await selectCase(response.data.case_id)
  message.value = '案例已创建，后续真实值与早期输入分别保存。'
}
async function uploadTruth(event) {
  const file = event.target.files?.[0]; if (!file || !caseId.value) return
  await run(async () => { await api.addTruth(caseId.value, { content: await file.text(), format: file.name.endsWith('.json') ? 'json' : 'csv' }); await reloadCases(); message.value = '真实后续数据已补充。' })
  event.target.value = ''
}
async function freeze() {
  const response = await api.freezeForecast(caseId.value, forecastForm.value)
  forecasts.value = (await api.listForecasts(caseId.value)).data
  message.value = `已冻结：${response.data.forecast_id}。可以计算误差或导出留档。`
}
async function evaluate(id) { evaluation.value = (await api.evaluateForecast(id)).data }
async function compare() { comparison.value = (await api.compareForecasts(selectedForecasts.value)).data }
function download(blob, name) {
  const link = document.createElement('a'), url = URL.createObjectURL(blob)
  link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
function downloadTemplate() {
  download(new Blob(['timestamp,volume,negative_ratio\n2026-01-01T00:00:00+08:00,10,0.2\n2026-01-01T01:00:00+08:00,15,0.3\n2026-01-01T02:00:00+08:00,20,0.4\n2026-01-01T03:00:00+08:00,25,0.45\n'], { type: 'text/csv;charset=utf-8' }), 'observations-template.csv')
}
async function exportSeed() { download(new Blob([(await api.getSeed(caseId.value)).data.text], { type: 'text/plain;charset=utf-8' }), `${caseId.value}-early-only.txt`) }
async function exportResult(format) { const id = evaluation.value.forecast.forecast_id; download(await api.exportEvaluation(id, format), `${id}.${format}`) }
async function createProject() {
  const seedData = (await api.getSeed(caseId.value)).data
  const seed = seedData.text
  const form = new FormData()
  form.append('files', new Blob([seed], { type: 'text/plain' }), 'early-observations.txt')
  form.append('project_name', selectedCase.value.name)
  form.append('research_case_id', caseId.value)
  form.append('simulation_requirement', seedData.requirement)
  const response = await generateOntology(form)
  router.push({ name: 'Process', params: { projectId: response.data.project_id } })
}
async function refreshUsage() { usage.value = (await api.getUsage(scopeFilter.value.trim())).data }
async function savePolicy() {
  const value = { ...policy.value, prices: { ...policy.value.prices } }
  if (priceForm.value.model.trim()) {
    if (priceForm.value.input === '' || priceForm.value.output === '') throw new Error('请填写输入和输出单价')
    const rates = { input: Number(priceForm.value.input), output: Number(priceForm.value.output) }
    if (priceForm.value.cached_input !== '') rates.cached_input = Number(priceForm.value.cached_input)
    value.prices[priceForm.value.model.trim()] = rates
  }
  policy.value = (await api.saveRuntimeSettings(value)).data; message.value = '运行设置已保存，新任务会使用这些设置。'
}
async function applyOptions() { await api.saveSimulationOptions(optionsId.value.trim(), options.value); message.value = '实验配置已固定到该实例。' }
async function loadPlan() { simulationPlan.value = (await api.getSimulationPlan(optionsId.value.trim())).data }
function chartPoints(rows) {
  const all = [...evaluation.value.scores.truth, ...evaluation.value.scores.prediction]
  const maximum = Math.max(1, ...all.map(row => row.volume))
  return rows.map((row, index) => `${35 + index / Math.max(rows.length - 1, 1) * 670},${210 - row.volume / maximum * 185}`).join(' ')
}
onMounted(() => run(async () => {
  const responses = await Promise.all([api.listCases(), api.getRuntimeSettings(), api.getUsage()])
  cases.value = responses[0].data; policy.value = responses[1].data; usage.value = responses[2].data
  if (route.query.caseId && cases.value.some(item => item.case_id === route.query.caseId)) await selectCase(route.query.caseId)
  else if (cases.value.length) await selectCase(cases.value[0].case_id)
}))
</script>

<style scoped>
nav.tabs{flex-wrap:wrap;align-items:center}nav.tabs a{font-size:13px;padding:11px 15px;text-decoration:none}
*{box-sizing:border-box}.research-page{min-height:100vh;background:#f4f3ef;color:#24282e;font-family:'Noto Sans SC',system-ui,sans-serif}.topbar{height:68px;padding:0 5%;display:flex;align-items:center;justify-content:space-between;background:#20252c;color:white}.brand{color:inherit;text-decoration:none;font-weight:800;letter-spacing:1px}.brand span{font-weight:400;color:#b8c0ca;font-size:13px}.local-label{font-size:12px;color:#b8c0ca}main{max-width:1440px;margin:auto;padding:38px 36px 80px}.intro{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-bottom:28px}.eyebrow{font-size:11px;letter-spacing:1.4px;color:#847970;margin:0 0 8px;overflow-wrap:anywhere}h1{font-size:32px;line-height:1.4;letter-spacing:-1px;margin:0 0 10px}h2{font-size:18px;margin:0 0 14px}.intro p:last-child{color:#687079;font-size:14px;margin:0}button{font:inherit;font-size:13px;font-weight:600;border:1px solid transparent;background:#ed632c;color:white;padding:11px 15px;cursor:pointer;border-radius:5px;transition:background .15s}button:hover{background:#d8511a}button:disabled{cursor:not-allowed;opacity:.48}.secondary{border-color:#d8d6d0;background:white;color:#343a42}.secondary:hover{background:#f2f1ed}.text-button{color:#c94b17;background:transparent;padding-left:0}.text-button:hover{background:transparent;text-decoration:underline}.full{width:100%}.tabs{display:flex;gap:6px;margin:24px 0}.tabs button{color:#6f747c;background:transparent;padding:11px 20px}.tabs button.selected{background:#20252c;color:white}.validation-layout{display:grid;grid-template-columns:285px minmax(0,1fr);gap:22px;align-items:start}.panel{background:white;border:1px solid #e2dfd8;border-radius:9px;padding:25px;min-width:0}.case-content{display:grid;gap:20px}.cases-panel{padding:23px 18px}.hint{font-size:12px;color:#747b83;line-height:1.8}.empty{font-size:13px;color:#81868c;padding:20px 0}.case-row{display:flex;flex-direction:column;text-align:left;width:100%;background:#f6f5f1;border:1px solid transparent;color:inherit;gap:8px;padding:13px;margin-bottom:10px;overflow-wrap:anywhere}.case-row:hover,.case-row.chosen{background:#fff5ef;border-color:#ed632c}.case-row span{font-size:11px;font-weight:400;color:#858079}.section-top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.badge{border:1px solid #deded9;color:#65716a;padding:7px 9px;border-radius:4px;font-size:11px;white-space:nowrap}.badge.demo{color:#a65a16;background:#fff7e9;border-color:#efdbc1}.case-facts{display:flex;flex-wrap:wrap;gap:15px;margin:15px 0;font-size:12px;color:#757c85}.case-facts strong{font-weight:500;color:#343a43;margin-left:5px}.button-row{display:flex;flex-wrap:wrap;gap:9px}.inline-upload{display:flex;gap:12px;align-items:center;margin-top:20px!important}label{display:block;font-size:12px;color:#626a73;margin:14px 0}input:not([type=checkbox]),select,textarea{display:block;width:100%;padding:10px 11px;margin-top:7px;font:inherit;font-size:13px;color:#24282e;border:1px solid #d9dbdd;border-radius:5px;background:#fff}input[type=file]{padding:8px!important;font-size:11px!important}input:focus,select:focus,textarea:focus{outline:2px solid #f0b59c;outline-offset:1px}input[type=checkbox]{width:16px;height:16px;accent-color:#ed632c}.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.field-grid label{min-width:0}details{margin-top:22px;border-top:1px solid #eeece6;padding-top:16px}summary{font-size:13px;font-weight:600;cursor:pointer}.notice{font-size:12px;line-height:1.8;background:#fff4e6;border:1px solid #edddc4;padding:11px 15px;border-radius:5px;margin-bottom:14px;overflow-wrap:anywhere}.notice.error{background:#fff0ee;color:#a73025;border-color:#edc3bc}.notice.success{background:#edf7f0;color:#306b47;border-color:#c9e1d0}.forecast-row{display:flex;align-items:center;gap:13px;padding:16px 0;border-bottom:1px solid #eee}.forecast-row div{flex:1;min-width:0}.forecast-row strong{font-size:13px}.forecast-row span{display:block;font-size:11px;margin-top:6px;color:#858990;overflow-wrap:anywhere}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}.metric-grid>div{background:#f7f6f2;border:1px solid #eeece7;border-radius:6px;padding:16px}.metric-grid span{display:block;font-size:11px;color:#7d8187;margin-bottom:10px}.metric-grid strong{font-size:25px;font-weight:600;letter-spacing:-.5px;overflow-wrap:anywhere}.metric-grid small{font-size:11px;font-weight:400;letter-spacing:0}.token-value{font-size:17px!important}.curve{margin:25px 0}.curve svg{width:100%;max-height:280px}.curve figcaption{font-size:11px;color:#747b83;display:flex;gap:20px;margin-bottom:10px}.legend:before{content:'';display:inline-block;width:16px;height:3px;margin:0 7px 3px 0;background:#20252c}.legend.predicted:before{background:#ed632c}table{width:100%;border-collapse:collapse;font-size:12px;text-align:left}caption{text-align:left;font-size:13px;font-weight:600;margin:10px 0}th{font-weight:500;color:#7d8187;background:#f7f6f2}th,td{padding:13px 12px;border-bottom:1px solid #eae8e3}td{overflow-wrap:anywhere}.table-scroll{overflow-x:auto}.fingerprint{font:10px monospace;color:#9a9a96;overflow-wrap:anywhere;margin-top:23px}.cost-line{font-size:13px;line-height:1.8}.cost-line strong{font-size:22px;margin:0 10px}.cost-line span{color:#9a713a}.settings-layout{display:grid;grid-template-columns:1fr 1fr;gap:22px;align-items:start}fieldset{border:1px solid #e3e1dc;margin:22px 0;padding:8px 16px 16px}legend{font-size:12px;color:#7b8188}.empty-state{min-height:340px;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center}.empty-state>p:last-child{font-size:13px;color:#8b8d91}@media(max-width:1000px){main{padding:28px 20px}.metric-grid{grid-template-columns:1fr 1fr}.validation-layout{grid-template-columns:240px minmax(0,1fr)}.section-top{flex-wrap:wrap}}@media(max-width:720px){.intro{align-items:flex-start;flex-direction:column}h1{font-size:27px}.validation-layout,.settings-layout{grid-template-columns:1fr}.panel{padding:19px}.case-facts{flex-direction:column;gap:8px}.field-grid{grid-template-columns:1fr}.inline-upload{display:block}.tabs button{padding:10px 15px}.local-label{display:none}}
</style>
