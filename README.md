# 实验指标分析与归因评估平台

支撑AB实验分组、指标口径计算、显著性检验、归因分析与报告分发的实验评估平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8022/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5202/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8022`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 事件接入 | `event_stream` | 事件定义 | 事件编码、事件名称、属性字段 |
| 实验设计 | `exp_design` | 实验方案 | 实验编码、实验名称、分组策略 |
| 分组分配器 | `variant_assign` | 分组结果 | 分配批次、用户标识、实验编码 |
| 指标口径中心 | `metric_catalog` | 指标定义 | 指标编码、指标名称、计算口径 |
| 指标计算作业 | `metric_compute` | 计算作业 | 作业名称、指标编码、计算窗口 |
| 显著性检验 | `significance_test` | 检验结果 | 检验批次、实验编码、指标编码 |
| 归因分析器 | `attr_analysis` | 归因结果 | 归因批次、实验编码、贡献因子 |
| 人群圈选器 | `cohort_builder` | 人群包 | 人群编码、人群名称、圈选条件 |
| 报告生成器 | `report_gen` | 分析报告 | 报告编码、实验编码、报告模板 |
| 报告分发 | `report_dist` | 分发记录 | 分发批次、报告编码、接收方 |
| 窗口调度器 | `window_scheduler` | 调度计划 | 计划名称、指标编码、窗口粒度 |
| 数据时效监控 | `data_freshness` | 时效记录 | 监控对象、数据域、最新水位 |
| 指标异常检测 | `anomaly_metric` | 异常事件 | 异常编号、指标编码、基线区间 |
| 实验归档库 | `exp_archive` | 归档实验 | 实验编码、结束时间、结论摘要 |
| 样本量核对 | `sample_ratio` | 核对结果 | 核对批次、实验编码、计划比例 |
| AA预检器 | `aa_check` | AA检验 | 检验编码、实验编码、检验窗口 |
| 切片分析器 | `segment_slice` | 切片结果 | 切片批次、实验编码、切片维度 |
| 曝光日志接入 | `exposure_log` | 曝光记录 | 日志批次、实验编码、用户标识 |
| 结论评审队列 | `conclusion_review` | 评审记录 | 记录编号、实验编码、评审结论 |
| 指标查询服务 | `metric_api` | 查询接口 | 接口编码、指标编码、服务对象 |

## 结构化公式 v2 迁移工作台

在原 20 个模块之外，新增「公式迁移工作台」（前端路由 `/migration`，左侧导航可进入），
并在 **指标口径中心 / 窗口调度器 / 指标计算作业** 三个原页面内嵌对应面板。

### 场景数据（服务启动自动引导，可幂等重置）

- 指标 `M`：旧文本公式 `COUNT(buy)/曝光量`（口径版本 v1，状态已生效），
  窗口 `W` 的最新一笔旧版计算 `RES-M-W-V1=0.128` 已登记为「已完成」作业（text-v1 快照，可重读）。
- 指标 `M2`：`COUNT(unknown_cart_event)/曝光量`，事件未在事件接入登记，预查结论「无法转换」。
- 指标 `M3`：`COUNT(DISTINCT user_id)/曝光量`，去重语义不在 v2 无损子集，同样阻断并列出原值/原因。

### 核心规则

- 解析器向服务端**真实事件表**（`event_stream`）与**分母注册表**核对：
  `COUNT(buy)/曝光量` →
  `{schema:'metric-formula/v2', numerator:{aggregation:'count', event:'buy'}, denominator:{code:'exposure_count',...}}`，
  完整保留事件与分母含义。
- 无法无损转换时返回 `blockers[].code/reason/原值`（如 `UNKNOWN_EVENT`、`UNKNOWN_DENOMINATOR`、
  `UNRECOGNIZED_EXPRESSION`、`UNSUPPORTED_AGGREGATION`、`LOSSY_DISTINCT`、`EVENT_NOT_VERIFIED`）。
- 迁移进度：`UNCHECKED → UNCONFIRMED（检查单已生成）→ ONLINE（确认切换/上线）/ BLOCKED`。
  进度未确认时调度领取返回 `ok:false, conflict:true, code:'MIGRATION_UNCONFIRMED'`；
  无法转换返回 `code:'FORMULA_NOT_CONVERTIBLE'` 且不派新版作业。
- 确认切换生成新旧映射 `MAP-*` 与同一套口径证据包 `EVP-*`（含 content_hash）。
- 窗口领取幂等：同一 `(指标, 窗口)` 只有一条切换记录、一个 v2 作业身份与一个有效结果；
  回滚后可续做（复用同一作业/证据/结果），重复确认的 `confirm_count` 恒为 1。
- 作业只绑定一种格式（`text-v1` / `structured-v2`）。用另一种格式读口径返回
  `code:'FORMAT_MISMATCH'`，杜绝同一作业混读两种格式。
- 调度只能领取带当前证据包的可执行 v2 作业；批量办理会跳过未确认/无法转换的指标。
- 已完成/已认领作业可读回开始时固化的口径快照、真实公式与事件依赖；
  旧公式↔旧作业↔历史查询接口的引用在页面与 `/references/{code}` 可查证，原历史查询
  `/history/query?metric_code=M&window=W` 仍按 text-v1 返回。

### 常用接口

```text
GET  /api/formula-v2/workbench                 # 工作台聚合：兼容结果/进度/映射/窗口/批次
POST /api/formula-v2/precheck                  # 全量或按 metric_code 预查
GET  /api/formula-v2/checklist/{metric_code}   # 服务端切换检查单
POST /api/formula-v2/metrics/{code}/confirm    # 确认切换/上线（幂等）
POST /api/formula-v2/windows/claim             # {metric_code, window} 领取 W
POST /api/formula-v2/windows/complete          # 完成并登记唯一有效结果
POST /api/formula-v2/windows/confirm           # 结果确认（同一窗口仅一次）
POST /api/formula-v2/windows/rollback-rerun    # 模拟回滚重跑/续做
POST /api/formula-v2/scheduler/dispatch        # 调度批量领取（阻断项跳过不派单）
GET  /api/formula-v2/jobs/{id}/definition[?format=text-v1|structured-v2]
GET  /api/formula-v2/references/{metric_code}  # 旧公式/旧作业/历史查询引用
GET  /api/formula-v2/history/query?...         # 原历史查询（旧口径仍有效）
GET  /api/formula-v2/batches/{batch_id}        # 上线批次的真实公式与事件依赖
POST /api/formula-v2/testing/reset             # 重置演示场景
```

同一组接口也挂在 `/api/migration/*`、`/api/formula-migration/*` 前缀；
通用动作接口 `POST /api/{metric_catalog|window_scheduler|metric_compute}/{id}/actions`
支持「预查公式 / 确认切换并上线 / 领取窗口作业 / 确认窗口结果 / 回滚重跑 / 读回口径」等中文动作及常见近义说法。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
