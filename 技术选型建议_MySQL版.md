# 技术选型建议（数据库改为MySQL）

## 1. 已确认
- 数据库：**MySQL 8.0+**（含空间类型）
- 部署方式：私有化部署
- 外部大模型：通过内网网关调用外部API

## 2. 推荐技术栈（除数据库外）

### 2.1 后端
- 语言/框架：Java 21 + Spring Boot 3（或 Go + Gin，二选一）
- API规范：OpenAPI 3.0（已生成 `openapi.yaml`）
- 鉴权：JWT + RBAC
- 任务调度：Celery/Argo Workflows/XXL-Job（按语言栈选择）
- 消息队列：RabbitMQ（任务编排）

### 2.2 三维与GIS
- 三维引擎：CesiumJS
- 瓦片服务：3D Tiles（模型预处理后发布）
- 空间计算：
  - 实时轻量：MySQL Spatial
  - 重计算离线：Python + PDAL/Open3D + 自研算法服务

### 2.3 AI与视觉
- 检测框架：PyTorch + YOLO系列（目标/缺陷）
- 推理服务：Triton Inference Server（可选）或 FastAPI
- 模型管理：MLflow（可选）

### 2.4 摄影测量与重建
- 引擎：OpenMVG/OpenMVS 或 商业引擎（按预算）
- 数据流程：影像入库 -> 重建任务 -> Mesh/DOM/DSM -> 切片发布

### 2.5 存储与基础设施
- 对象存储：MinIO
- 缓存：Redis
- 日志：ELK / Loki + Grafana
- 容器化：Docker + Kubernetes（私有集群）

### 2.6 外部大模型网关
- 服务：`llm-gateway-service`
- 能力：供应商适配、脱敏、审计、限流、回退
- 原则：仅发送最小必要字段，不出域原始图纸/影像/点云

## 3. 关键注意事项（MySQL）
1. 使用 MySQL 8.0.30+，确保空间SRID行为稳定。
2. 大规模空间分析不要全部压在MySQL中，复杂差分建议离线算法服务计算后回写结果。
3. 若后续出现高并发空间查询瓶颈，可加一层专用空间引擎或引入分层缓存。
