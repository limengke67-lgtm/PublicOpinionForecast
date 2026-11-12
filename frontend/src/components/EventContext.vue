<template>
  <aside v-if="eventId" class="event-context">
    <RouterLink :to="`/events/${eventId}`">← 返回事件任务</RouterLink>
    <span>返回事件查看关联记录与综合报告。</span>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const eventId = computed(() => route.query.eventId)
</script>

<style scoped>
.event-context{display:flex;align-items:center;gap:18px;padding:11px 24px;background:#fff1e3;border-bottom:1px solid #ebd8bd;font:12px/1.7 system-ui;color:#796346;flex-wrap:wrap}.event-context a{color:#b44f20;font-weight:650;text-decoration:none}
</style>
