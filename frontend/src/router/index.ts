import { createRouter, createWebHashHistory } from 'vue-router'
import ChatView from '@/views/ChatView.vue'

const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'chat', component: ChatView },
    {
      path: '/files',
      name: 'files',
      component: () => import('@/views/DbFilesView.vue'),
    },
    {
      path: '/feedback',
      name: 'feedback',
      component: () => import('@/views/FeedbackView.vue'),
    },
    {
      path: '/tokens',
      name: 'tokens',
      component: () => import('@/views/TokensView.vue'),
    },
  ],
})

export default router
