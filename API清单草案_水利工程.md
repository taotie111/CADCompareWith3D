# API清单草案（水利工程：CAD对比实景）

Base URL（私有化）：`/api/v1`

## 1. 认证与权限

### 1.1 登录
- `POST /auth/login`
- 入参：`username`, `password`
- 出参：`access_token`, `expires_in`, `roles`

### 1.2 获取当前用户
- `GET /auth/me`

---

## 2. 项目与场景管理

### 2.1 创建项目
- `POST /projects`
- 入参：
```json
{
  "name": "XX水闸试点",
  "code": "SLUICE-001",
  "coordinate_system": "CGCS2000",
  "location": "湖北-XX"
}
```

### 2.2 项目列表
- `GET /projects?page=1&page_size=20`

### 2.3 创建场景批次（一次航测/一次比对）
- `POST /projects/{project_id}/scenes`
- 入参：`scene_name`, `capture_time`, `description`

---

## 3. 数据接入（CAD/影像/控制点）

### 3.1 上传CAD文件
- `POST /ingest/cad/upload`（multipart）
- 出参：`file_id`, `status`

### 3.2 提交CAD解析任务
- `POST /ingest/cad/parse-jobs`
- 入参：
```json
{
  "project_id": "...",
  "scene_id": "...",
  "file_id": "...",
  "mapping_template_id": "default-water-v1"
}
```

### 3.3 上传无人机影像包
- `POST /ingest/uav/upload`（multipart）

### 3.4 提交重建任务（SfM/MVS）
- `POST /ingest/recon/jobs`
- 入参：`project_id`, `scene_id`, `image_set_id`, `quality_profile`

### 3.5 上传控制点（GCP）
- `POST /ingest/gcp`
- 入参：CSV/JSON

---

## 4. 模型与配准

### 4.1 查询模型资源
- `GET /models?project_id=...&scene_id=...&type=design|reality`

### 4.2 提交配准任务（GCP+ICP）
- `POST /registration/jobs`
- 入参：
```json
{
  "project_id": "...",
  "scene_id": "...",
  "design_model_id": "...",
  "reality_model_id": "...",
  "method": ["gcp", "icp"]
}
```

### 4.3 配准结果
- `GET /registration/jobs/{job_id}`
- 出参：`rmse_xy`, `rmse_z`, `transform_matrix`, `confidence`

---

## 5. 差异检测与结果管理

### 5.1 提交差异检测任务
- `POST /diff/jobs`
- 入参：
```json
{
  "project_id": "...",
  "scene_id": "...",
  "registration_job_id": "...",
  "modes": ["geometry", "semantic", "rule"]
}
```

### 5.2 查询差异结果
- `GET /diff/results?project_id=...&scene_id=...&risk_level=high`

### 5.3 差异结果复核
- `POST /diff/results/{result_id}/review`
- 入参：`status`（confirmed/rejected）, `comment`

---

## 6. AI识别服务

### 6.1 提交识别任务
- `POST /ai/vision/jobs`
- 入参：`project_id`, `scene_id`, `model_profile`, `targets`

### 6.2 查询识别结果
- `GET /ai/vision/results?scene_id=...&label=crack`

---

## 7. 风险引擎与预警

### 7.1 规则集管理
- `GET /risk/rulesets`
- `POST /risk/rulesets/import`（支持 yaml/json）

### 7.2 风险评估执行
- `POST /risk/evaluate/jobs`
- 入参：`project_id`, `scene_id`, `ruleset_id`

### 7.3 风险事件列表
- `GET /risk/events?level=high&status=open`

### 7.4 风险事件详情
- `GET /risk/events/{event_id}`

---

## 8. 工单闭环

### 8.1 创建工单
- `POST /workorders`
- 入参：`event_id`, `assignee`, `deadline`

### 8.2 工单流转
- `POST /workorders/{id}/transition`
- 入参：`action`（accept/resolve/reopen/close）

---

## 9. 三维可视化接口（Cesium）

### 9.1 获取tileset地址
- `GET /visual/tilesets?scene_id=...`
- 出参：`design_tileset_url`, `reality_tileset_url`, `diff_overlay_url`

### 9.2 获取热区与告警点
- `GET /visual/hotspots?scene_id=...&level=high`

---

## 10. 外部大模型网关（LLM Gateway）

### 10.1 风险说明生成
- `POST /llm/risk-explain`
- 入参：
```json
{
  "event_id": "...",
  "language": "zh-CN",
  "style": "engineer"
}
```

### 10.2 处置建议生成
- `POST /llm/remediation-plan`
- 入参：`event_id`, `constraints`, `output_format`

### 10.3 规范问答（RAG）
- `POST /llm/spec-qa`
- 入参：`question`, `project_context`, `top_k`

---

## 11. 通用任务接口

### 11.1 查询任务状态
- `GET /jobs/{job_id}`

### 11.2 任务取消
- `POST /jobs/{job_id}/cancel`

---

## 12. 统一响应建议

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
  "request_id": "req_xxx",
  "timestamp": "2026-04-16T10:00:00Z"
}
```

错误码分段建议：
- 1xxx 参数/权限
- 2xxx 数据接入
- 3xxx 算法任务
- 4xxx 风险引擎
- 5xxx 外部模型网关
