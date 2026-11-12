<template>
  <div class="events-page">
    <header><RouterLink to="/" class="brand">PublicOpinionForecast <span>舆情预测</span></RouterLink><nav><RouterLink :to="eventId ? contextLink('/research', { caseId: workspace?.case.case_id }) : '/research'">验证与费用</RouterLink><RouterLink :to="eventId ? contextLink('/research/rolling', { sessionId: workspace?.rolling?.session_id }) : '/research/rolling'">滚动分析</RouterLink><RouterLink to="/explore">自由推演</RouterLink></nav></header>
    <main>
      <div class="intro"><div><p class="eyebrow">从一个真实问题，到一份可核验的研究结果</p><h1>{{ eventId ? workspace?.event.name || '正在读取事件…' : '管理你的舆情预测事件。' }}</h1><p>材料、图谱、推演、验证、纠偏和报告，保存在同一个事件里。</p></div><button v-if="!eventId" :disabled="busy" @click="run(demo)">体验完整合成流程 ↗</button><button v-else class="secondary" :disabled="busy" @click="run(loadWorkspace)">刷新事件状态</button></div>
      <div v-if="error" class="notice error" role="alert">{{ error }}</div><div v-if="message" class="notice success" role="status">{{ message }}</div>

      <section v-if="!eventId" class="hub-layout">
        <div class="event-grid"><p v-if="!events.length" class="empty">暂无事件，可以创建新任务或先体验合成流程。</p><RouterLink v-for="item in events" :key="item.event_id" :to="`/events/${item.event_id}`" class="panel event-card"><span class="badge">{{ item.is_demo ? '合成演示' : '观测研究' }}</span><h2>{{ item.name }}</h2><p>观测窗口 {{ item.case_ids.length }} 组 · 冻结预测 {{ item.forecast_ids.length }} 份 · 综合报告 {{ item.report_ids.length }} 份</p><small>{{ displayTime(item.updated_at) }} →</small></RouterLink></div>
        <form class="panel create-panel" @submit.prevent="run(create)"><h2>创建事件任务</h2><label>事件名称<input v-model="form.name" maxlength="120" placeholder="例如：校园服务调整后的讨论走势" /></label><label>已有观测案例<select v-model="form.case_id"><option value="">导入新的时间序列</option><option v-for="item in cases" :key="item.case_id" :value="item.case_id">{{ item.name }}</option></select></label>
          <template v-if="!form.case_id"><label>观测 CSV / JSON<input type="file" accept=".csv,.json" required @change="readCase" /></label><p class="hint">字段 timestamp、volume，可选 negative_ratio。时间为桶结束时刻，须带时区，至少 3 个连续早期桶。</p><label>观测截止时间<input v-model="form.case.cutoff" required placeholder="2026-01-01T11:00:00+08:00" /></label><div class="fields"><label>每桶小时<input v-model.number="form.case.bucket_hours" type="number" min="1" max="168" required /></label><label>未来桶数<input v-model.number="form.case.horizon" type="number" min="1" max="500" required /></label></div><label>截止前主体与已知事实<textarea v-model="form.case.background" rows="4" maxlength="20000" placeholder="参与主体、已发生事件和可靠来源。请勿包含未来信息。" /></label><label>平台<input v-model="form.case.source.platform" /></label><label>来源名称<input v-model="form.case.source.source_name" /></label><label>采样口径<textarea v-model="form.case.source.sampling_method" rows="2" /></label></template>
          <button :disabled="busy || (!form.case_id && !form.case.content)" class="full">创建事件</button><RouterLink class="text-link" to="/research/rolling">需要先清洗原始帖子？打开数据准备工具 ↗</RouterLink>
        </form>
      </section>

      <template v-if="workspace && eventId">
        <div v-if="workspace.event.is_demo" class="notice">当前为合成流程演示。免费数值基线和本地校准可以跑通流程，不代表已经完成真实多智能体预测。</div>
        <div v-if="!workspace.model_ready" class="notice">本地研究流程可用。模型与 Zep 配置待后续补齐，在线推演入口暂不可用。</div>
        <ol class="stages"><li v-for="(stage, index) in workspace.stages" :key="stage.name" :class="stage.status"><span>{{ index + 1 }}</span>{{ stage.name }}</li></ol>
        <div class="detail-layout">
          <div class="content">
            <section class="panel"><p class="eyebrow">01 / 已知事实</p><h2>当前观测窗口</h2><div class="facts"><span>截至 {{ displayTime(workspace.case.cutoff) }}</span><span>{{ workspace.case.train.length }} 个早期桶</span><span>每桶 {{ workspace.case.bucket_hours }} 小时</span><span>预测 {{ workspace.case.horizon }} 桶</span></div><p class="hint">来源：{{ workspace.case.source?.source_name || '待填写 / 核验' }} · {{ workspace.case.source?.sampling_method || '采样口径待补充' }}</p><details><summary>查看背景材料</summary><p class="background">{{ workspace.case.background || '尚未填写。语义推演前请补齐截止前主体与事实。' }}</p></details><RouterLink :to="contextLink('/research', { caseId: workspace.case.case_id })" class="text-link">查看材料、初始预测与独立评分 ↗</RouterLink></section>

            <section class="panel"><p class="eyebrow">02 / 图谱与多智能体</p><h2>准备和运行推演</h2><p class="hint">系统自动关联当前事件的项目和仿真。旧项目、旧图谱与旧报告保留，新窗口使用独立图谱。</p>
              <div v-if="workspace.latest_simulation?.lifecycle === 'unmanaged'" class="notice">发现磁盘中的运行记录，但当前服务没有接管该进程。请核查本机旧进程；系统不会假装已恢复角色状态，也不会自动重复付费推演。可保留旧记录并建立独立重试窗口。</div>
              <div v-if="workspace.latest_project?.error || workspace.latest_simulation?.error" class="notice error">{{ workspace.latest_simulation?.error || workspace.latest_project?.error }}</div>
              <div class="button-row"><button :disabled="busy || !workspace.model_ready" @click="run(startProject)">{{ workspace.latest_project ? '新建独立推演（调用模型）' : '创建图谱与推演（调用模型）' }}</button><RouterLink v-if="workspace.latest_project" class="button secondary" :to="contextLink(`/process/${workspace.latest_project.project_id}`)">继续图谱 / 角色准备</RouterLink><RouterLink v-if="workspace.latest_simulation" class="button secondary" :to="contextLink(`/simulation/${workspace.latest_simulation.simulation_id}/start`)">打开仿真运行页</RouterLink></div>
              <div v-if="workspace.latest_simulation" class="facts"><span>当前状态 {{ workspace.latest_simulation.lifecycle }}</span><span>角色 {{ workspace.latest_simulation.profiles_count }}</span><span>已完成 {{ workspace.latest_simulation.run?.current_round || 0 }} / {{ workspace.latest_simulation.run?.total_rounds || '—' }} 轮</span></div>
            </section>

            <section class="panel"><p class="eyebrow">03 / 冻结预测</p><h2>{{ workspace.rolling ? '接入本轮新参考预测' : '建立初始预测与滚动任务' }}</h2><form @submit.prevent="run(freeze)"><div class="fields"><label>预测来源<select v-model="freezeForm.method"><option value="simulation">已完成的多智能体仿真</option><option value="moving_average">免费：近 3 桶均值基线</option><option value="last_value">免费：保持最近值基线</option></select></label><label>接收方式<select v-model="freezeForm.mode"><option value="replay">历史回放</option><option value="live">实际接收新观测</option></select></label></div><template v-if="freezeForm.method === 'simulation'"><label>当前事件的仿真<select v-model="freezeForm.simulation_id"><option value="">自动选择当前窗口已完成的最新仿真</option><option v-for="item in workspace.simulations" :key="item.simulation_id" :value="item.simulation_id">{{ displayTime(item.created_at) }} / {{ item.lifecycle }}</option></select></label><div class="fields"><label>平台<select v-model="freezeForm.platform"><option value="reddit">Reddit / Community</option><option value="twitter">Twitter / Plaza</option></select></label><label>数量映射<select v-model="freezeForm.scale_mode"><option value="history_anchor">早期锚定相对热度</option><option value="raw">原始发帖与评论计数</option></select></label></div></template><p class="hint">冻结后自动关联滚动任务。免费基线没有使用多智能体；新推演必须匹配当前已知观测，过期预测不会冒充更新后的参考。</p><button :disabled="busy">冻结并自动关联</button></form></section>

            <section v-if="workspace.rolling" class="panel"><p class="eyebrow">04 / 现实反馈</p><h2>接收观测、验证和更新</h2><div class="metrics"><div><small>已验证桶</small><strong>{{ workspace.rolling.summary.scored_buckets }}</strong></div><div><small>当前方案 MAE</small><strong>{{ numeric(workspace.rolling.summary.metrics.predicted?.mae) }}</strong></div><div><small>预测版本</small><strong>{{ workspace.rolling.versions.length }}</strong></div></div><div class="notice">{{ latest.calibration.reason }}</div>
              <form @submit.prevent="run(ingest)"><div class="fields"><label>下一桶结束时间<input v-model="observation.timestamp" required /></label><label>实际发帖 / 评论数量<input v-model.number="observation.volume" type="number" min="0" max="1000000000000" step="any" required /></label></div><label>本次采集说明<input v-model="observationNote" maxlength="2000" /></label><label>新增已知事实（可选）<textarea v-model="contextText" rows="2" maxlength="5000" /></label><div class="button-row"><button :disabled="busy">接收并自动评分、更新</button><button v-if="workspace.rolling.mode === 'replay'" type="button" class="secondary" :disabled="busy || latest.cutoff >= (workspace.rolling.replay_end || workspace.rolling.versions[0].prediction.at(-1).timestamp)" @click="run(replay)">释放下一桶历史观测</button></div><label>批量 CSV / JSON<input type="file" accept=".csv,.json" @change="uploadObservations" /></label></form>
              <div class="button-row"><RouterLink class="button secondary" :to="contextLink('/research/rolling', { sessionId: workspace.rolling.session_id })">版本曲线与高级分析</RouterLink><button class="secondary" :disabled="busy" @click="run(refreshCase)">生成下一轮材料 / 独立重试窗口</button></div><p class="hint">本地数字校准追加模型请求为零。新事实需要语义推演时，先生成新的材料窗口，再进入上面的图谱与推演步骤。</p>
            </section>

            <section class="panel"><p class="eyebrow">05 / 可交付成果</p><h2>统一综合报告</h2><p class="hint">汇总数据来源、仿真状态、冻结预测、误差、费用、校准轨迹和匹配的语义报告。证据不完整时明确标注，不补造结果。每次报告保存独立快照。</p><button :disabled="busy" @click="run(makeReport)">生成综合报告（无模型调用）</button><label v-if="workspace.reports.length">历史报告<select v-model="reportId" @change="run(loadReport)"><option value="">选择报告</option><option v-for="(item, index) in workspace.reports" :key="item.report_id" :value="item.report_id">报告 {{ index + 1 }} / {{ displayTime(item.created_at) }} {{ item.stale ? '（已有新证据）' : '（当前）' }}</option></select></label><template v-if="report"><div class="button-row"><button v-for="format in ['md', 'html', 'json']" :key="format" class="secondary" :disabled="busy" @click="run(() => downloadReport(format))">导出 {{ format.toUpperCase() }}</button></div><details open><summary>报告内容</summary><pre class="report-text">{{ report.markdown }}</pre></details></template></section>
          </div>
          <aside class="side-column"><section class="panel"><h2>事件用量</h2><template v-if="workspace.usage"><p class="cost">{{ workspace.usage.totals.estimated_cny == null ? '费用尚不能完整估算' : `¥${workspace.usage.totals.estimated_cny.toFixed(4)}` }}</p><p class="hint">模型请求 {{ workspace.usage.totals.requests }} 次 · 输入 / 输出 Token {{ workspace.usage.totals.input_tokens }} / {{ workspace.usage.totals.output_tokens }}</p></template><p v-else class="hint">尚无关联模型账本记录。免费数值步骤不会调用模型。</p><p class="hint">Zep 费用另计；金额按实际配置单价估算。未运行的多智能体流程不能当成零成本实测。</p><RouterLink :to="contextLink('/research')" class="text-link">单价、预算与运行计划 ↗</RouterLink></section><section class="panel"><h2>操作记录</h2><ol class="timeline"><li v-for="(item, index) in workspace.event.timeline.slice().reverse()" :key="index"><strong>{{ item.detail }}</strong><small>{{ displayTime(item.time) }}</small></li></ol></section><section class="panel"><h2>恢复与交付</h2><p class="hint">刷新或服务重启后，事件关系和已保存预测保留。中断的推演需要检查旧进程后独立重试；不会声称已恢复未保存的角色内部状态。</p><RouterLink to="/" class="text-link">返回全部事件 ↗</RouterLink></section></aside>
        </div>
      </template>
    </main><footer>PublicOpinionForecast · Based on <a href="https://github.com/666ghj/MiroFish" target="_blank" rel="noreferrer">MiroFish</a> · AGPL-3.0</footer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as api from '../api/events'
import * as research from '../api/research'
import { generateOntology } from '../api/graph'

const route = useRoute(), router = useRouter()
const eventId = computed(() => route.params.eventId)
const events = ref([]), cases = ref([]), workspace = ref(null), busy = ref(false), error = ref(''), message = ref('')
const form = ref({ name: '', case_id: '', case: { name: '', content: '', format: 'csv', cutoff: '', bucket_hours: 1, horizon: 6, background: '', source: { platform: '', source_name: '', sampling_method: '', timezone_offset_hours: 8 } } })
const freezeForm = ref({ method: 'simulation', mode: 'replay', simulation_id: '', platform: 'reddit', scale_mode: 'history_anchor' })
const observation = ref({ timestamp: '', volume: '' }), observationNote = ref(''), contextText = ref('')
const report = ref(null), reportId = ref(''), timer = ref(null)
const latest = computed(() => workspace.value?.rolling?.versions.at(-1))
const numeric = value => value == null || !Number.isFinite(value) ? '—' : Number(value.toFixed(3)).toLocaleString()
const displayTime = value => value ? new Date(value).toLocaleString() : '—'
const contextLink = (path, query = {}) => ({ path, query: { ...query, eventId: eventId.value } })

async function run(task) {
  if (busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try { await task() } catch (cause) { error.value = cause.message || '操作失败' } finally { busy.value = false }
}
function mutation(extra = {}) { return { expected_revision: workspace.value.event.revision, request_id: crypto.randomUUID(), ...extra } }
function setWorkspace(value) {
  const previous = workspace.value?.rolling?.revision
  workspace.value = value
  if (value.rolling && (previous !== value.rolling.revision || !observation.value.timestamp)) {
    observation.value = { timestamp: new Date(+new Date(value.rolling.observations.at(-1).timestamp) + value.rolling.bucket_hours * 3600000).toISOString(), volume: '' }
  }
}
async function loadHub() {
  const values = await Promise.all([api.listEvents(), research.listCases()])
  events.value = values[0].data; cases.value = values[1].data
}
async function loadWorkspace() { if (eventId.value) setWorkspace((await api.getEvent(eventId.value)).data) }
async function demo() { const value = (await api.createEventDemo()).data; await router.push(`/events/${value.event.event_id}`); await loadWorkspace() }
async function create() {
  const payload = form.value.case_id ? { name: form.value.name, case_id: form.value.case_id } : { name: form.value.name, case: { ...form.value.case, name: form.value.name || '未命名事件' } }
  const value = (await api.createEvent(payload)).data
  await router.push(`/events/${value.event_id}`)
  await loadWorkspace()
}
async function readCase(event) { await run(async () => { const value = await readFile(event); if (value) Object.assign(form.value.case, value) }) }
async function readFile(event) {
  const file = event.target.files?.[0]; if (!file) return null
  if (file.size > 10 * 1024 * 1024) throw new Error('文件不能超过 10 MB')
  return { content: await file.text(), format: file.name.toLowerCase().endsWith('.json') ? 'json' : 'csv' }
}
async function startProject() {
  const caseId = workspace.value.case.case_id
  const seed = (await research.getSeed(caseId)).data
  const data = new FormData()
  data.append('files', new Blob([seed.text], { type: 'text/plain' }), 'early-observations.txt')
  data.append('project_name', workspace.value.event.name); data.append('research_case_id', caseId)
  data.append('simulation_requirement', seed.requirement)
  const value = (await generateOntology(data)).data
  await router.push(contextLink(`/process/${value.project_id}`))
}
async function freeze() { setWorkspace((await api.freezeEvent(eventId.value, mutation(freezeForm.value))).data); message.value = '预测已冻结，事件和滚动任务已自动关联。' }
async function ingest() {
  setWorkspace((await api.ingestEvent(eventId.value, mutation({ rows: [observation.value], source_note: observationNote.value, context_text: contextText.value }))).data)
  contextText.value = ''; message.value = '已接收真实观测并生成新的预测版本。'
}
async function replay() { setWorkspace((await api.replayEventStep(eventId.value, mutation())).data); message.value = '已释放一桶历史观测，评分和更新完成。' }
async function uploadObservations(event) {
  await run(async () => { const data = await readFile(event); if (data) setWorkspace((await api.ingestEvent(eventId.value, mutation({ ...data, source_note: observationNote.value, context_text: contextText.value }))).data) })
  event.target.value = ''
}
async function refreshCase() {
  setWorkspace((await api.refreshEventCase(eventId.value, mutation())).data)
  message.value = '新窗口已建立。现在可在第二步创建独立图谱和新推演，旧记录保留。'
}
async function makeReport() {
  report.value = (await api.createEventReport(eventId.value, mutation())).data; reportId.value = report.value.report_id
  await loadWorkspace(); message.value = '综合报告已生成并保存为独立快照。'
}
async function loadReport() { report.value = reportId.value ? (await api.getEvidence(reportId.value)).data : null }
async function downloadReport(format) {
  const blob = await api.exportEvidence(report.value.report_id, format)
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = `${report.value.report_id}.${format}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function load() {
  report.value = null; reportId.value = ''; workspace.value = null
  if (eventId.value) await loadWorkspace(); else await loadHub()
}
watch(eventId, () => run(load))
onMounted(() => {
  run(load)
  timer.value = setInterval(() => { if (eventId.value && !busy.value) loadWorkspace().catch(() => {}) }, 6000)
})
onBeforeUnmount(() => clearInterval(timer.value))
</script>

<style scoped>
*{box-sizing:border-box}.events-page{min-height:100vh;background:#f4f3ef;color:#25313b;font-family:'Noto Sans SC',system-ui,sans-serif}header{min-height:70px;background:#212931;color:white;display:flex;align-items:center;justify-content:space-between;padding:18px 5%;gap:20px}header a{color:inherit;text-decoration:none}.brand{font-weight:800;font-size:18px;letter-spacing:.2px}.brand span{font-size:12px;color:#b5bfc7;margin-left:12px;font-weight:400}nav{display:flex;gap:22px;font-size:12px}main{max-width:1440px;margin:auto;padding:38px 30px 70px}.intro{display:flex;justify-content:space-between;align-items:center;gap:24px;margin-bottom:28px}.eyebrow{font-size:11px;letter-spacing:1px;color:#8c8175;margin:0 0 10px}h1{font-size:32px;line-height:1.5;margin:0 0 10px}h2{font-size:18px;margin:0 0 16px}.intro p:last-child{color:#73808a;font-size:14px}.panel{background:white;border:1px solid #e0ddd6;border-radius:9px;padding:25px;min-width:0}.hub-layout{display:grid;grid-template-columns:minmax(0,1fr) 355px;gap:24px;align-items:start}.event-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.event-card{display:block;text-decoration:none;color:inherit;transition:border-color .2s}.event-card:hover{border-color:#e86631}.event-card h2{margin-top:18px;line-height:1.6}.event-card p{font-size:12px;color:#77828b;line-height:1.8}.event-card small{display:block;font-size:11px;color:#9b8c7d;margin-top:20px}.badge{font-size:11px;color:#986f46;padding:6px 9px;background:#fff3e4;border-radius:4px}.hint{font-size:12px;color:#75818b;line-height:1.85}.notice{padding:13px 16px;border:1px solid #eddac1;border-radius:5px;background:#fff5e7;font-size:12px;line-height:1.8;margin:16px 0;overflow-wrap:anywhere}.notice.error{background:#fff0ec;color:#a83a2c;border-color:#e9c5bb}.notice.success{background:#eef7f0;color:#366c47;border-color:#c9dfcf}.stages{list-style:none;display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:25px 0;padding:0}.stages li{font-size:12px;padding:13px 10px;color:#889097;background:#e9e7e0;border-radius:5px;display:flex;align-items:center;gap:9px}.stages span{display:inline-flex;width:23px;height:23px;background:white;border-radius:50%;justify-content:center;align-items:center}.stages .completed{background:#e5f1e7;color:#3b6e48}.stages .attention{background:#fff0d9;color:#9b6a23}.detail-layout{display:grid;grid-template-columns:minmax(0,1fr) 285px;gap:23px;align-items:start}.content,.side-column{display:grid;gap:20px}label{display:block;font-size:12px;color:#62717b;margin:14px 0;min-width:0}input,select,textarea{display:block;width:100%;padding:10px 11px;margin-top:7px;font:inherit;font-size:13px;color:#283641;border:1px solid #d9dde0;border-radius:5px;background:white}input[type=file]{font-size:11px;padding:8px}input:focus,select:focus,textarea:focus{outline:2px solid #f0b49b;outline-offset:1px}button,.button{display:inline-block;text-align:center;text-decoration:none;font:inherit;font-size:13px;font-weight:650;padding:11px 15px;border:1px solid transparent;border-radius:5px;background:#e96630;color:white;cursor:pointer}button:hover,.button:hover{background:#d85321}button:disabled{cursor:not-allowed;opacity:.43}.secondary{background:white;border-color:#d9d7cf;color:#3b4a55}.secondary:hover{background:#f7f5ef}.full{width:100%}.text-link{display:inline-block;color:#c34f20;font-size:12px;line-height:1.8;text-decoration:none;margin-top:16px}.fields{display:grid;grid-template-columns:1fr 1fr;gap:14px}.button-row{display:flex;flex-wrap:wrap;gap:10px;margin:15px 0}.facts{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;line-height:1.8;color:#6b7a85;margin:15px 0}details{margin:18px 0;border-top:1px solid #eeeae2;padding-top:14px}summary{font-size:12px;font-weight:650;cursor:pointer}.background{white-space:pre-wrap;font-size:12px;line-height:1.9;color:#71808a;margin-top:12px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}.metrics>div{background:#f7f6f1;border:1px solid #eeece3;border-radius:5px;padding:16px}.metrics small{display:block;font-size:11px;color:#7d8990;margin-bottom:10px}.metrics strong{font-size:26px;overflow-wrap:anywhere}.report-text{white-space:pre-wrap;overflow-wrap:anywhere;max-height:600px;overflow-y:auto;background:#f8f7f3;padding:17px;font:12px/1.85 system-ui;color:#566670;margin-top:15px}.timeline{list-style:none;padding:0;max-height:600px;overflow-y:auto}.timeline li{padding:13px 0;border-bottom:1px solid #eeeae2}.timeline strong{display:block;font-size:12px;line-height:1.8;font-weight:500}.timeline small{display:block;font-size:10px;color:#9a9e9e;margin-top:7px}.cost{font-size:19px;line-height:1.7;color:#a05d2e}.empty{padding:30px;color:#8a9399;font-size:13px}footer{text-align:center;padding:22px;font-size:11px;color:#8a9399}footer a{color:inherit}@media(max-width:1100px){.event-grid{grid-template-columns:1fr}.hub-layout{grid-template-columns:minmax(0,1fr) 330px}.detail-layout{grid-template-columns:minmax(0,1fr) 245px}}@media(max-width:760px){header{align-items:flex-start;flex-direction:column;padding:18px 20px;gap:12px}nav{gap:18px;flex-wrap:wrap}.brand span{display:block;margin:5px 0 0}main{padding:25px 18px 50px}.intro{align-items:flex-start;flex-direction:column}h1{font-size:27px}.hub-layout,.detail-layout,.fields{grid-template-columns:1fr}.stages{grid-template-columns:1fr 1fr}.panel{padding:20px}.metrics{gap:8px}.metrics>div{padding:12px}.metrics strong{font-size:22px}.create-panel{order:-1}.facts{flex-direction:column;gap:6px}}
</style>
