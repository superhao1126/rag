# 前端项目说明（Frontend Overview）

> Vue 3 + Vite + Ant Design Vue 单页应用。提供 RAG 知识问答的全部交互界面：对话、文件管理、反馈分析、Token 用量。

---

## 1. 技术栈

| 类别 | 选型 | 版本 |
| --- | --- | --- |
| 运行时 | **Node.js 24** | `^20.19.0 \|\| >=22.12.0`，仓库锁定 24 |
| 包管理 | **pnpm 10.30+** | 不要混用 npm/yarn，会导致 lock 冲突 |
| 框架 | Vue 3 (Composition API + `<script setup>`) | ^3.5 |
| 构建工具 | Vite 8 | 含 vite-plugin-vue-devtools |
| 语言 | **TypeScript ~6.0** + vue-tsc 3.x | 严格类型 |
| UI 库 | **Ant Design Vue 4.2** + `@ant-design/icons-vue` | 表格 / 表单 / 弹窗 / 消息 |
| 状态管理 | **Pinia 3** | 单 store `session` |
| 路由 | vue-router 5 | hash 模式 |
| HTTP | axios 1.15 | 统一响应解包 + 错误提示 |
| 图表 | **echarts 5.5** + 自封装 `<EChart>` | 按需引入：BarChart/LineChart + 常用 Component + CanvasRenderer |
| Markdown | marked 18 + highlight.js 11 | 答案渲染 + 代码高亮 |
| Lint / Format | oxlint 1.60 + eslint 10 + oxfmt 0.45 | `pnpm run lint` 一键 |
| 类型检查 | `vue-tsc --build` | `pnpm run type-check` |

---

## 2. 目录结构

```
frontend/
├── index.html              # SPA 入口
├── index-legacy.html       # 旧入口（保留备用）
├── package.json
├── pnpm-lock.yaml          # ← 必须提交，禁止删
├── vite.config.ts
├── tsconfig*.json
├── eslint.config.ts
├── .oxlintrc.json / .oxfmtrc.json
├── env.d.ts                # 全局类型补全
├── public/                 # 直接拷贝的静态资源
├── dist/                   # 构建产物（被后端 server.py 挂载）
│
└── src/
    ├── main.ts             # Pinia + Router + AntD 装配
    ├── App.vue             # 根布局：AppHeader + RouterView
    ├── assets/             # 全局 css / 图片
    │
    ├── router/             # vue-router 路由表（4 个 view）
    ├── stores/
    │   └── session.ts      # Pinia：当前会话、消息高亮、LLM 统计缓存
    ├── composables/
    │   └── useToast.ts     # AntD message 简化封装
    │
    ├── api/                # 所有后端调用（按业务拆分）
    │   ├── http.ts         # axios 实例 + 统一拦截器（接受 2xx）
    │   ├── index.ts        # 聚合导出 api 默认对象
    │   ├── types.ts        # 全部接口的 TS 类型定义
    │   ├── files.ts        # 文件上传（快速 + SSE 流式）/ 列表 / 删除 / 分块
    │   ├── query.ts        # 问答接口（SSE）
    │   ├── conversations.ts# 会话 CRUD
    │   ├── feedback.ts     # 反馈提交 + 聚合
    │   ├── stats.ts        # LLM token 统计 / 调用流水 / 趋势
    │   └── sse.ts          # 通用 SSE 工具
    │
    ├── components/
    │   ├── AppHeader.vue       # 顶栏导航 + 主题切换
    │   ├── ChunkModal.vue      # 分块预览弹窗（按文件/全量）
    │   ├── ChunkSourcePanel.vue# 单文件分块统计 + 列表
    │   └── EChart.vue          # ★ 通用 ECharts 包装组件
    │
    └── views/
        ├── ChatView.vue        # 主对话界面（左：会话列表 / 右：消息流）
        ├── DbFilesView.vue     # 文件管理（上传 + 列表 + 后台处理轮询）
        ├── FeedbackView.vue    # 反馈分析（趋势图 + 原因图 + 明细列表）
        └── TokensView.vue      # Token 用量（趋势 + 模型聚合 + 调用流水）
```

---

## 3. 关键页面与组件

### 3.1 ChatView.vue
- 左侧会话列表（增删改查），点击切换；右侧消息流（用户/助手气泡 + 引用 chunk）。
- 流式回答通过 `api/sse.ts` 解析 SSE，逐 token 追加到当前消息。
- 消息可点赞/点踩/写原因 → 调 `api.submitFeedback`。
- 引用块点击打开 `ChunkModal` 预览。

### 3.2 DbFilesView.vue
- **傻瓜化上传**：调 `POST /api/upload`，落盘后立即 `202` → 前端进入 `processing` 状态 → `waitForBackendProcessing` 每 2s 轮询 `/api/files`，最多 60 次（约 2 分钟），出现新文件即视为完成。
- 上传任务状态机：`uploading → processing → done / error`，对应 4 套 CSS 边框色。
- 列表展示 `id / source / chunk_count / total_chars / uploaded_at`，可删除、可预览分块。

### 3.3 FeedbackView.vue
- 顶部 5 张统计卡（总回答 / 已反馈 / 👍 / 👎 / 满意度）。
- **反馈趋势**：ECharts 堆叠柱（👍 绿 + 👎 红），近 7/30 天可切换。
- **原因分布**：ECharts 横向条形图（按数量升序）。
- 明细列表点击跳转回对应会话并高亮消息。

### 3.4 TokensView.vue
- 顶部 4 张统计卡（输入 / 输出 / 合计 / 调用次数）。
- **Token 用量趋势**：ECharts 堆叠柱(对话输入+对话输出) + 折线(向量化)，桶 hour/day 切换、范围 12/24/48 或 7/14/30。
- 按调用类型聚合（chat/title/embedding 标签）。
- 按模型聚合表格 + 分页调用流水（含过滤）。

### 3.5 EChart.vue（通用包装）
- 按需引入：BarChart / LineChart + GridComponent / LegendComponent / TitleComponent / TooltipComponent / DataZoomComponent + CanvasRenderer。
- ResizeObserver 自适应容器尺寸。
- MutationObserver 监听 `<html data-theme>` 切换 → 重绘。
- 卸载时 `dispose` 释放 canvas。
- 调用方只关心 `option`：`<EChart :option="option" :loading="loading" style="height:240px" />`。

---

## 4. API 层约定

```ts
// 所有接口返回统一外壳
interface ApiResponse<T> { code: number; message: string; data: T }

// http.ts 拦截器：code 在 [200,300) 视为成功，否则抛错并 toast
// 业务代码只接到 res.data
const res = await api.getLlmStats();
console.log(res.data.totals.total_tokens);
```

- 上传**不要**用 `api.upload` 之外的方式拼 multipart；流式接口 `uploadStream` / `uploadWithProgress` 走 `/api/upload/stream`。
- SSE 统一用 `api/sse.ts` 的工具函数（带超时与异常重连）。

---

## 5. 环境与开发

### 5.1 环境要求
- **Node.js 24.x**（仓库锁定）
- **pnpm 10.30+**（`packageManager` 字段强制）
- 推荐 VSCode + Vue Official + ESLint 插件

### 5.2 安装与启动

```bash
cd frontend

# 1. 安装依赖（首次或 lock 变更后）
pnpm install

# 2. 开发模式（默认 5173 端口；vite.config.ts 可改 host/port）
pnpm run dev

# 3. 生产构建（输出到 dist/，被后端 server.py 自动挂载）
pnpm run build

# 4. 类型检查
pnpm run type-check

# 5. lint + 自动修复
pnpm run lint
```

### 5.3 与后端联调

- 开发时把 `vite.config.ts` 的 `server.proxy` 指向 `http://localhost:8000`（如未配置可临时加）。
- 或者：先 `pnpm build` 后启动后端，由 FastAPI 直接托管 `dist/`，访问 `http://localhost:8000`。

---

## 6. 注意事项

### 6.1 包管理

- **必须用 pnpm**。`package.json` 的 `packageManager` 字段已锁版本，混用 npm/yarn 会破坏 lock 与 hoisting。
- 添加依赖：`pnpm add <pkg>`；添加 dev 依赖：`pnpm add -D <pkg>`。
- 新增了 `echarts ^5.5.1`，**首次拉取后必须 `pnpm install`** 才能 dev/build。

### 6.2 ECharts 用法

- **统一用 `<EChart>` 包装组件**，不要在 view 里直接 `echarts.init`。
- 颜色保持系统 token：蓝 `#1677ff`、绿 `#52c41a` / `#22c55e`、橙 `#fa8c16`、红 `#ef4444`。
- 暗色模式：在 option 里读 `document.documentElement.dataset.theme`，组件已自动监听切换。
- 按需引入新图表：在 `EChart.vue` 顶部 `echarts.use([...])` 里追加，不要 `import 'echarts'` 全量。

### 6.3 类型安全

- `api/types.ts` 是接口契约源头，后端字段调整必须同步（最近一次：`KbFile` 字段是 `total_chars`，**不是** `char_count`）。
- 所有 axios 调用必须带泛型：`axios.get<ApiResponse<KbFile[]>>(...)`。

### 6.4 SSE / 流式

- SSE 连接占用一个浏览器并发，长时间不关会触发 6 连接上限；一定要在组件 `onBeforeUnmount` 里 `eventSource.close()`。
- 后端断流时前端要做"已接收的内容仍展示 + 提示重试"，不要直接清空。

### 6.5 上传体验

- 上传成功的"语义"是**落盘 + 排队**，不是"已可检索"。前端通过轮询文件列表确认完成，超时 2 分钟后提示用户手动刷新。
- 大文件（>50MB）建议提前在后端加分片上传；当前实现一次性 multipart。

### 6.6 主题 / 布局

- 暗色主题通过 `<html data-theme="dark">` 切换；CSS 变量在 `assets/` 全局定义。
- AntD 的暗色 token 通过 `ConfigProvider` 注入（如尚未启用，可在 `App.vue` 加）。

### 6.7 构建产物

- `dist/` **不入库**（在 `.gitignore`）。生产部署流程是：构建产物 → 后端容器内同时挂载。
- `index-legacy.html` 是旧入口备份，可忽略。

---

## 7. 调试速查

| 现象 | 检查 |
| --- | --- |
| 接口请求 4xx 但前端无错误提示 | 看 `api/http.ts` 拦截器；当前接受 2xx，4xx/5xx 会 toast |
| 上传后列表不刷新 | 看 `waitForBackendProcessing` 是否仍在轮询；后端是否真的入库（去 PG `rag_chunks` 查） |
| 图表空白 | 容器是否有显式高度（`<EChart style="height:240px">`） |
| 切换主题后图表颜色不变 | 检查 EChart 是否监听到 attribute 变化（看 `MutationObserver`） |
| ts 编译报 `KbFile` 字段不存在 | 同步 `api/types.ts` 定义 |
| pnpm 报 lifecycle script 失败 | 删 `node_modules` + 重 `pnpm install`，确认 Node 版本 24.x |

---

## 8. 后续可优化

- 引入 vue-echarts（声明式封装），减少手动 dispose / resize 模板代码
- 抽离 axios 错误码 → 业务消息映射表，集中维护
- ChatView 引入虚拟列表（消息上千条性能下降）
- 使用 vite 的 SSR / 预加载，提升首屏
- 加 i18n（vue-i18n）支持多语言
