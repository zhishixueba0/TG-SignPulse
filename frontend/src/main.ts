import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import { useToast } from './composables/useToast'

const app = createApp(App)
// 全局兜底：Layout 错误边界之外的渲染错误（如 App 级组件）也不再静默，
// 记入控制台并弹 toast。Layout 边界返回 false 的错误不会走到这里，不会重复上报。
app.config.errorHandler = (err) => {
  // eslint-disable-next-line no-console
  console.error('[SignPulse] 应用级错误', err)
  try {
    useToast().error('页面渲染失败')
  } catch {
    // toast 不可用时忽略，只保留控制台记录
  }
}
app.use(createPinia())
app.use(router)
app.use(i18n)
app.mount('#app')
