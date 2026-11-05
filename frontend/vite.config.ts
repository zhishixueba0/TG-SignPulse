/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'TG-SignPulse',
        short_name: 'SignPulse',
        description: 'Telegram Automation Panel',
        // 与 index.html 首帧脚本的深色主题一致：深色启动屏避免安装后闪白；
        // 运行时标签栏颜色由 useTheme 动态同步 theme-color meta
        theme_color: '#0f172a',
        background_color: '#0f172a',
        lang: 'zh-CN',
        display: 'standalone',
        start_url: '/',
        icons: [
          {
            src: '/pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png'
          },
          {
            src: '/pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png'
          }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        // 注意：这里刻意不配 runtimeCaching 缓存 /api/*。
        // 之前配过 NetworkFirst 缓存常规 GET API：Service Worker 会把
        // /api/proxies 这类动态列表响应也写进 api-cache，同步完成前
        // 抓到的空列表可能被再次返回给页面，导致池子里已有数据、页面
        // 却一直显示为空；而且 API 请求本身已带 cache: "no-store"。
        // 静态资源走 precache（按内容哈希版本化）已足够，API 一律走网络。
      }
    })
  ],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
        ws: true,
      }
    }
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    setupFiles: ['src/test/setup.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/lib/**/*.ts', 'src/stores/**/*.ts', 'src/composables/**/*.ts'],
      exclude: ['src/test/**', 'src/**/*.d.ts'],
    },
  },
})
