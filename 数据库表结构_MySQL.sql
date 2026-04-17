-- 水利工程 CAD对比实景系统（MySQL 8）
-- 初始化脚本 v1
-- 建议版本：MySQL 8.0.30+

CREATE DATABASE IF NOT EXISTS cad_compare
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE cad_compare;

-- 1) 项目与场景
CREATE TABLE IF NOT EXISTS projects (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  code VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  coordinate_system VARCHAR(64) NOT NULL DEFAULT 'CGCS2000',
  location TEXT,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS scenes (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  project_id CHAR(36) NOT NULL,
  scene_name VARCHAR(255) NOT NULL,
  capture_time DATETIME NULL,
  version_no INT NOT NULL DEFAULT 1,
  status VARCHAR(32) NOT NULL DEFAULT 'draft',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_scenes_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  INDEX idx_scenes_project (project_id)
) ENGINE=InnoDB;

-- 2) 文件与模型资源
CREATE TABLE IF NOT EXISTS assets (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  project_id CHAR(36) NOT NULL,
  scene_id CHAR(36) NULL,
  asset_type VARCHAR(32) NOT NULL,
  file_name VARCHAR(512) NOT NULL,
  uri TEXT NOT NULL,
  checksum VARCHAR(128) NULL,
  size_bytes BIGINT NULL,
  metadata JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_assets_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_assets_scene FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE SET NULL,
  INDEX idx_assets_project_scene (project_id, scene_id),
  INDEX idx_assets_type (asset_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS models (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  project_id CHAR(36) NOT NULL,
  scene_id CHAR(36) NOT NULL,
  model_type VARCHAR(32) NOT NULL,
  source_asset_id CHAR(36) NULL,
  format VARCHAR(32) NULL,
  srid INT NOT NULL DEFAULT 4490,
  bbox POLYGON SRID 4490 NULL,
  quality_score DECIMAL(5,2) NULL,
  metadata JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_models_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_models_scene FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
  CONSTRAINT fk_models_asset FOREIGN KEY (source_asset_id) REFERENCES assets(id) ON DELETE SET NULL,
  INDEX idx_models_project_scene (project_id, scene_id),
  INDEX idx_models_type (model_type)
) ENGINE=InnoDB;

-- 3) CAD语义对象
CREATE TABLE IF NOT EXISTS design_objects (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  project_id CHAR(36) NOT NULL,
  scene_id CHAR(36) NOT NULL,
  model_id CHAR(36) NULL,
  object_code VARCHAR(128) NULL,
  object_type VARCHAR(64) NOT NULL,
  layer_name VARCHAR(128) NULL,
  properties JSON NULL,
  geom GEOMETRY SRID 4490 NOT NULL,
  elevation_m DECIMAL(10,3) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_design_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_design_scene FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
  CONSTRAINT fk_design_model FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL,
  INDEX idx_design_obj_project_scene (project_id, scene_id),
  INDEX idx_design_obj_type (object_type),
  SPATIAL INDEX idx_design_obj_geom (geom)
) ENGINE=InnoDB;

-- 4) 任务体系
CREATE TABLE IF NOT EXISTS jobs (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  project_id CHAR(36) NULL,
  scene_id CHAR(36) NULL,
  job_type VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  progress DECIMAL(5,2) NOT NULL DEFAULT 0,
  input_payload JSON NULL,
  output_payload JSON NULL,
  error_message TEXT NULL,
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_jobs_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_jobs_scene FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE SET NULL,
  INDEX idx_jobs_project_scene (project_id, scene_id),
  INDEX idx_jobs_type_status (job_type, status)
) ENGINE=InnoDB;

-- 5) 配准结果
CREATE TABLE IF NOT EXISTS registration_results (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  job_id CHAR(36) NOT NULL,
  project_id CHAR(36) NOT NULL,
  scene_id CHAR(36) NOT NULL,
  design_model_id CHAR(36) NULL,
  reality_model_id CHAR(36) NULL,
  rmse_xy_m DECIMAL(10,4) NULL,
  rmse_z_m DECIMAL(10,4) NULL,
  confidence DECIMAL(5,4) NULL,
  transform_matrix JSON NOT NULL,
  gcp_count INT NULL,
  metadata JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_reg_job FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  CONSTRAINT fk_reg_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_reg_scene FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
  CONSTRAINT fk_reg_design_model FOREIGN KEY (design_model_id) REFERENCES models(id) ON DELETE SET NULL,
  CONSTRAINT fk_reg_reality_model FOREIGN KEY (reality_model_id) REFERENCES models(id) ON DELETE SET NULL,
  INDEX idx_reg_project_scene (project_id, scene_id)
) ENGINE=InnoDB;

-- 6) AI识别结果
CREATE TABLE IF NOT EXISTS ai_detections (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  job_id CHAR(36) NULL,
  project_id CHAR(36) NOT NULL,
  scene_id CHAR(36) NOT NULL,
  label VARCHAR(128) NOT NULL,
  score DECIMAL(5,4) NOT NULL,
  geom GEOMETRY SRID 4490 NULL,
  bbox_2d JSON NULL,
  attributes JSON NULL,
  evidence_asset_id CHAR(36) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_ai_job FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL,
  CONSTRAINT fk_ai_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_ai_scene FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
  CONSTRAINT fk_ai_asset FOREIGN KEY (evidence_asset_id) REFERENCES assets(id) ON DELETE SET NULL,
  INDEX idx_ai_det_project_scene (project_id, scene_id),
  INDEX idx_ai_det_label (label)
) ENGINE=InnoDB;

-- 7) 差异结果
CREATE TABLE IF NOT EXISTS diff_results (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  job_id CHAR(36) NULL,
  project_id CHAR(36) NOT NULL,
  scene_id CHAR(36) NOT NULL,
  registration_result_id CHAR(36) NULL,
  diff_type VARCHAR(32) NOT NULL,
  object_id CHAR(36) NULL,
  object_type VARCHAR(64) NULL,
  metric_name VARCHAR(64) NULL,
  metric_value DECIMAL(12,5) NULL,
  threshold_value DECIMAL(12,5) NULL,
  risk_level VARCHAR(16) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'new',
  geom GEOMETRY SRID 4490 NULL,
  evidence JSON NULL,
  reviewed_by VARCHAR(128) NULL,
  reviewed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_diff_job FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL,
  CONSTRAINT fk_diff_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_diff_scene FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
  CONSTRAINT fk_diff_reg FOREIGN KEY (registration_result_id) REFERENCES registration_results(id) ON DELETE SET NULL,
  INDEX idx_diff_project_scene (project_id, scene_id),
  INDEX idx_diff_type_level (diff_type, risk_level)
) ENGINE=InnoDB;

-- 8) 规则与风险事件
CREATE TABLE IF NOT EXISTS risk_rulesets (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  project_id CHAR(36) NULL,
  name VARCHAR(255) NOT NULL,
  version VARCHAR(64) NOT NULL,
  content JSON NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 0,
  created_by VARCHAR(128) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_ruleset_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  INDEX idx_ruleset_project_active (project_id, is_active)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS risk_events (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  project_id CHAR(36) NOT NULL,
  scene_id CHAR(36) NOT NULL,
  source_diff_id CHAR(36) NULL,
  source_ai_id CHAR(36) NULL,
  rule_id VARCHAR(128) NULL,
  risk_type VARCHAR(128) NOT NULL,
  level VARCHAR(16) NOT NULL,
  title VARCHAR(255) NULL,
  description TEXT NULL,
  suggestion TEXT NULL,
  location POINT SRID 4490 NULL,
  evidence JSON NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'open',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_risk_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_risk_scene FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE CASCADE,
  CONSTRAINT fk_risk_diff FOREIGN KEY (source_diff_id) REFERENCES diff_results(id) ON DELETE SET NULL,
  CONSTRAINT fk_risk_ai FOREIGN KEY (source_ai_id) REFERENCES ai_detections(id) ON DELETE SET NULL,
  INDEX idx_risk_project_scene (project_id, scene_id),
  INDEX idx_risk_level_status (level, status)
) ENGINE=InnoDB;

-- 9) 工单闭环
CREATE TABLE IF NOT EXISTS work_orders (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  event_id CHAR(36) NOT NULL,
  assignee VARCHAR(128) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'open',
  deadline DATETIME NULL,
  resolution_note TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_workorder_event FOREIGN KEY (event_id) REFERENCES risk_events(id) ON DELETE CASCADE,
  INDEX idx_workorder_event (event_id),
  INDEX idx_workorder_status (status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS work_order_logs (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  work_order_id CHAR(36) NOT NULL,
  action VARCHAR(64) NOT NULL,
  operator VARCHAR(128) NULL,
  comment TEXT NULL,
  payload JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_workorder_logs_wo FOREIGN KEY (work_order_id) REFERENCES work_orders(id) ON DELETE CASCADE,
  INDEX idx_workorder_logs_wo (work_order_id)
) ENGINE=InnoDB;

-- 10) 外部大模型调用审计
CREATE TABLE IF NOT EXISTS llm_calls (
  id CHAR(36) PRIMARY KEY DEFAULT (UUID()),
  project_id CHAR(36) NULL,
  scene_id CHAR(36) NULL,
  event_id CHAR(36) NULL,
  provider VARCHAR(64) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  endpoint_alias VARCHAR(128) NULL,
  prompt_template_id VARCHAR(128) NULL,
  input_tokens INT NULL,
  output_tokens INT NULL,
  latency_ms INT NULL,
  status VARCHAR(32) NOT NULL,
  request_hash VARCHAR(128) NULL,
  response_summary TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_llm_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
  CONSTRAINT fk_llm_scene FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE SET NULL,
  CONSTRAINT fk_llm_event FOREIGN KEY (event_id) REFERENCES risk_events(id) ON DELETE SET NULL,
  INDEX idx_llm_calls_project_scene (project_id, scene_id),
  INDEX idx_llm_calls_status (status)
) ENGINE=InnoDB;
