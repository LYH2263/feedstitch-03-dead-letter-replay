<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'

const batches = ref([])
const openId = ref(null)
const detail = ref(null)
const err = ref('')

async function load() {
  batches.value = await getJSON('/api/replay-batches')
}

async function toggle(id) {
  if (openId.value === id) {
    openId.value = null
    detail.value = null
    return
  }
  openId.value = id
  detail.value = await getJSON(`/api/replay-batches/${id}`)
}

onMounted(async () => {
  try {
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>
<template>
  <div class="page">
    <h1>重放批次</h1>
    <p class="muted">
      死信重放批次报告。处理总数 = 写入主数据 + 关闭 + 作废；Canonical 条数取批次前后实测值，
      与 Canonical 列表页同源。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>批次</th><th>状态</th><th>处理总数</th><th>写入主数据</th>
            <th>关闭</th><th>作废</th><th>Canonical 前→后（Δ）</th><th>完成时间</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in batches" :key="b.id">
            <td>#{{ b.id }}</td>
            <td><span class="tag" :class="b.status === 'ok' ? 'ok' : 'warn'">{{ b.status }}</span></td>
            <td><strong>{{ b.total_count }}</strong></td>
            <td>{{ b.written_count }}</td>
            <td>{{ b.closed_count }}</td>
            <td>{{ b.discarded_count }}</td>
            <td class="mono">{{ b.canonical_before }} → {{ b.canonical_after }}（{{ b.report.canonical_delta }}）</td>
            <td class="mono">{{ b.finished_at }}</td>
            <td><button class="secondary" @click="toggle(b.id)">{{ openId === b.id ? '收起' : '明细' }}</button></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!batches.length" class="muted">暂无重放批次。先到冲突队列裁决并重放。</p>
    </div>

    <div class="panel" v-if="detail">
      <h3>批次 #{{ detail.id }} 对账明细</h3>
      <p class="muted">{{ detail.report.reconciliation }}</p>
      <table>
        <thead>
          <tr><th>冲突</th><th>业务键</th><th>裁决</th><th>是否改主数据</th><th>结果说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="it in detail.items" :key="it.id">
            <td>#{{ it.conflict_id }}</td>
            <td class="mono">{{ it.business_key }}</td>
            <td>{{ it.resolution }}</td>
            <td>
              <span class="tag" :class="it.canonical_changed ? 'ok' : 'warn'">
                {{ it.canonical_changed ? '是' : '否' }}
              </span>
            </td>
            <td class="muted">{{ it.result_note }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
