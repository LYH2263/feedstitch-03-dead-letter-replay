<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const rows = ref([])
const counts = ref({ pending: 0, adjudicated: 0, replayed: 0, total: 0 })
const filter = ref('all')
const selected = ref(new Set())
const err = ref('')
const busy = ref(false)
const lastBatch = ref(null)
const openDetail = ref(new Set())

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待处理' },
  { key: 'adjudicated', label: '已裁决' },
  { key: 'replayed', label: '已处理' },
]

const STATUS_META = {
  pending: { label: '待处理', cls: 'warn' },
  adjudicated: { label: '已裁决', cls: 'accent' },
  replayed: { label: '已处理', cls: 'ok' },
}
const RESOLUTION_META = {
  overwrite: '接受传入·覆盖写入',
  keep: '保留主数据·关闭',
  discard: '作废·忽略',
}

const selectedIds = computed(() => Array.from(selected.value))
const selectedAdjudicated = computed(() =>
  rows.value
    .filter((c) => selected.value.has(c.id) && c.status === 'adjudicated')
    .map((c) => c.id),
)
const allChecked = computed(() => rows.value.length > 0 && rows.value.every((c) => selected.value.has(c.id)))

async function load() {
  const qs = filter.value === 'all' ? '' : `?status=${filter.value}`
  rows.value = await getJSON(`/api/conflicts${qs}`)
  counts.value = await getJSON('/api/conflicts/counts')
  // Drop selections that left the current filter view.
  const visible = new Set(rows.value.map((c) => c.id))
  selected.value = new Set([...selected.value].filter((id) => visible.has(id)))
}

async function refresh() {
  try {
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function toggleRow(id) {
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selected.value = next
}

function toggleAll() {
  if (allChecked.value) selected.value = new Set()
  else selected.value = new Set(rows.value.map((c) => c.id))
}

function toggleDetail(id) {
  const next = new Set(openDetail.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  openDetail.value = next
}

async function adjudicate(resolution) {
  if (!selectedIds.value.length) return
  err.value = ''
  busy.value = true
  try {
    await postJSON('/api/conflicts/adjudicate', {
      conflict_ids: selectedIds.value,
      resolution,
    })
    selected.value = new Set()
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function replay() {
  if (!selectedAdjudicated.value.length) return
  err.value = ''
  busy.value = true
  try {
    lastBatch.value = await postJSON('/api/replay-batches', {
      conflict_ids: selectedAdjudicated.value,
    })
    selected.value = new Set()
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

onMounted(refresh)
</script>
<template>
  <div class="page">
    <h1>冲突队列</h1>
    <p class="muted">
      同业务键、非键字段分歧 → quarantine，摄取期不写 Canonical。先裁决，再按批次重放；
      仅「接受传入」会覆盖写入主数据，其余裁决不得改动主数据。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <div class="count-bar">
        <span>总计 <strong>{{ counts.total }}</strong></span>
        <span class="tag warn">待处理 {{ counts.pending }}</span>
        <span class="tag accent">已裁决 {{ counts.adjudicated }}</span>
        <span class="tag ok">已处理 {{ counts.replayed }}</span>
      </div>
      <div class="filter-bar">
        <button
          v-for="f in FILTERS"
          :key="f.key"
          class="secondary"
          :class="{ active: filter === f.key }"
          @click="filter = f.key; refresh()"
        >
          {{ f.label }}
        </button>
      </div>
    </div>

    <div class="panel action-bar">
      <div class="muted">已选 {{ selectedIds.length }} 条 · 其中已裁决可重放 {{ selectedAdjudicated.length }} 条</div>
      <div class="action-buttons">
        <button :disabled="busy || !selectedIds.length" @click="adjudicate('overwrite')">
          接受传入 · 覆盖写入
        </button>
        <button class="secondary" :disabled="busy || !selectedIds.length" @click="adjudicate('keep')">
          保留主数据 · 关闭
        </button>
        <button class="secondary" :disabled="busy || !selectedIds.length" @click="adjudicate('discard')">
          作废 · 忽略
        </button>
        <button class="replay" :disabled="busy || !selectedAdjudicated.length" @click="replay">
          发起重放批次（{{ selectedAdjudicated.length }}）
        </button>
      </div>
    </div>

    <div class="panel" v-if="lastBatch">
      <h3>重放批次 #{{ lastBatch.id }} 报告 <span class="tag ok">{{ lastBatch.status }}</span></h3>
      <p>
        处理总数 <strong>{{ lastBatch.total_count }}</strong> =
        写入主数据 <strong>{{ lastBatch.written_count }}</strong> +
        关闭 <strong>{{ lastBatch.closed_count }}</strong> +
        作废 <strong>{{ lastBatch.discarded_count }}</strong>
      </p>
      <p class="mono muted">
        新增 {{ lastBatch.report.inserted_count }} · 覆盖更新 {{ lastBatch.report.updated_count }} ·
        Canonical {{ lastBatch.canonical_before }} → {{ lastBatch.canonical_after }}
        （Δ {{ lastBatch.report.canonical_delta }}）· {{ lastBatch.finished_at }}
      </p>
      <p class="muted">{{ lastBatch.report.reconciliation }}</p>
    </div>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th style="width:34px"><input type="checkbox" :checked="allChecked" @change="toggleAll" /></th>
            <th>ID</th><th>业务键</th><th>状态</th><th>裁决</th><th>裁决时间 / 结果</th><th>批次</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in rows" :key="c.id">
            <td><input type="checkbox" :checked="selected.has(c.id)" @change="toggleRow(c.id)" /></td>
            <td>#{{ c.id }}<div class="muted mono">run {{ c.run_id }}</div></td>
            <td class="mono">{{ c.business_key }}<div class="muted">{{ c.reason }}</div></td>
            <td><span class="tag" :class="STATUS_META[c.status].cls">{{ STATUS_META[c.status].label }}</span></td>
            <td>{{ c.resolution ? RESOLUTION_META[c.resolution] : '—' }}</td>
            <td class="mono muted" style="font-size:0.78rem">
              <div>{{ c.resolved_at || '—' }}</div>
              <div>{{ c.result_note || '' }}</div>
            </td>
            <td class="mono">{{ c.replay_batch_id ? '#' + c.replay_batch_id : '—' }}</td>
            <td><button class="secondary" @click="toggleDetail(c.id)">{{ openDetail.has(c.id) ? '收起' : '对比' }}</button></td>
          </tr>
          <tr v-for="c in rows.filter((x) => openDetail.has(x.id))" :key="'d' + c.id" class="detail-row">
            <td></td>
            <td colspan="7">
              <div class="diff-grid">
                <div>
                  <div class="muted">existing（主数据侧）</div>
                  <pre class="mono">{{ JSON.stringify(c.existing, null, 2) }}</pre>
                </div>
                <div>
                  <div class="muted">incoming（传入侧）</div>
                  <pre class="mono">{{ JSON.stringify(c.incoming, null, 2) }}</pre>
                </div>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="muted">当前筛选下暂无冲突。</p>
    </div>
  </div>
</template>
<style scoped>
.count-bar {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  margin-bottom: 0.75rem;
}
.filter-bar {
  display: flex;
  gap: 0.5rem;
}
.filter-bar .active {
  background: var(--accent);
  color: #041018;
}
.action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}
.action-buttons {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
button.replay {
  background: var(--ok);
}
.tag.accent {
  background: color-mix(in srgb, var(--accent) 22%, transparent);
  color: var(--accent);
}
.detail-row td {
  background: #0a1422;
}
.diff-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}
pre {
  background: #0a1422;
  padding: 0.75rem;
  border-radius: 8px;
  overflow: auto;
  max-height: 240px;
  margin: 0.25rem 0 0;
}
</style>
