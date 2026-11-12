import { createRouter, createWebHistory } from 'vue-router'
import Home from '../views/Home.vue'
import Process from '../views/MainView.vue'
import SimulationView from '../views/SimulationView.vue'
import SimulationRunView from '../views/SimulationRunView.vue'
import ReportView from '../views/ReportView.vue'
import InteractionView from '../views/InteractionView.vue'
import ResearchView from '../views/ResearchView.vue'

const routes = [
  { path: '/research/rolling', name: 'Rolling', component: () => import('../views/RollingView.vue') },
  { path: '/research', name: 'Research', component: ResearchView },
  {
    path: '/',
    name: 'Home',
    component: () => import('../views/EventsView.vue')
  },
  { path: '/events/:eventId', name: 'Event', component: () => import('../views/EventsView.vue') },
  { path: '/explore', name: 'Explore', component: Home },
  {
    path: '/process/:projectId',
    name: 'Process',
    component: Process,
    props: true
  },
  {
    path: '/simulation/:simulationId',
    name: 'Simulation',
    component: SimulationView,
    props: true
  },
  {
    path: '/simulation/:simulationId/start',
    name: 'SimulationRun',
    component: SimulationRunView,
    props: true
  },
  {
    path: '/report/:reportId',
    name: 'Report',
    component: ReportView,
    props: true
  },
  {
    path: '/interaction/:reportId',
    name: 'Interaction',
    component: InteractionView,
    props: true
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, from) => {
  const workflow = ['Process', 'Simulation', 'SimulationRun', 'Report', 'Interaction', 'Research', 'Rolling', 'Explore']
  if (workflow.includes(to.name) && !to.query.eventId && from.query.eventId) {
    return { path: to.path, query: { ...to.query, eventId: from.query.eventId }, hash: to.hash }
  }
})

export default router
