-- 水利工程 CAD对比实景系统（PostgreSQL + PostGIS）
-- 初始化脚本 v0

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1) 项目与场景
CREATE TABLE IF NOT EXISTS projects (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  code VARCHAR(64) UNIQUE NOT NULL,
  name VARCHAR(255) NOT NULL,
  coordinate_system VARCHAR(64) NOT NULL DEFAULT 'CGCS2000',
  location TEXT,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scenes (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  scene_name VARCHAR(255) NOT NULL,
  capture_time TIMESTAMPTZ,
  version_no INT NOT NULL DEFAULT 1,
  status VARCHAR(32) NOT NULL DEFAULT 'draft',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_scenes_project ON scenes(project_id);

-- 2) 文件与模型资源
CREATE TABLE IF NOT EXISTS assets (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  scene_id UUID REFERENCES scenes(id) ON DELETE SET NULL,
  asset_type VARCHAR(32) NOT NULL, -- cad/uav_image/pointcloud/mesh/dom/dsm/tileset/evidence
  file_name VARCHAR(512) NOT NULL,
  uri TEXT NOT NULL,
  checksum VARCHAR(128),
  size_bytes BIGINT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_assets_project_scene ON assets(project_id, scene_id);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(asset_type);

CREATE TABLE IF NOT EXISTS models (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  scene_id UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
  model_type VARCHAR(32) NOT NULL, -- design/reality
  source_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
  format VARCHAR(32), -- ifc/obj/ply/3dtiles
  srid INT NOT NULL DEFAULT 4490,
  bbox GEOMETRY(Polygon, 4490),
  quality_score NUMERIC(5,2),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_models_project_scene ON models(project_id, scene_id);
CREATE INDEX IF NOT EXISTS idx_models_type ON models(model_type);
CREATE INDEX IF NOT EXISTS idx_models_bbox_gix ON models USING GIST (bbox);

-- 3) CAD语义对象
CREATE TABLE IF NOT EXISTS design_objects (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  scene_id UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
  model_id UUID REFERENCES models(id) ON DELETE SET NULL,
  object_code VARCHAR(128),
  object_type VARCHAR(64) NOT NULL, -- dam_axis/sluice_axis/pier/gate_slot/...
  layer_name VARCHAR(128),
  properties JSONB NOT NULL DEFAULT '{}'::jsonb,
  geom GEOMETRY(Geometry, 4490) NOT NULL,
  elevation_m NUMERIC(10,3),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_design_obj_project_scene ON design_objects(project_id, scene_id);
CREATE INDEX IF NOT EXISTS idx_design_obj_type ON design_objects(object_type);
CREATE INDEX IF NOT EXISTS idx_design_obj_geom_gix ON design_objects USING GIST (geom);

-- 4) 任务体系
CREATE TABLE IF NOT EXISTS jobs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  scene_id UUID REFERENCES scenes(id) ON DELETE SET NULL,
  job_type VARCHAR(64) NOT NULL, -- cad_parse/reconstruction/registration/diff/vision/risk_eval
  status VARCHAR(32) NOT NULL DEFAULT 'queued', -- queued/running/success/failed/cancelled
  progress NUMERIC(5,2) NOT NULL DEFAULT 0,
  input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_message TEXT,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_project_scene ON jobs(project_id, scene_id);
CREATE INDEX IF NOT EXISTS idx_jobs_type_status ON jobs(job_type, status);

-- 5) 配准结果
CREATE TABLE IF NOT EXISTS registration_results (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  scene_id UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
  design_model_id UUID REFERENCES models(id),
  reality_model_id UUID REFERENCES models(id),
  rmse_xy_m NUMERIC(10,4),
  rmse_z_m NUMERIC(10,4),
  confidence NUMERIC(5,4),
  transform_matrix JSONB NOT NULL,
  gcp_count INT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reg_project_scene ON registration_results(project_id, scene_id);

-- 6) AI识别结果
CREATE TABLE IF NOT EXISTS ai_detections (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  scene_id UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
  label VARCHAR(128) NOT NULL,
  score NUMERIC(5,4) NOT NULL,
  geom GEOMETRY(Geometry, 4490),
  bbox_2d JSONB,
  attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_det_project_scene ON ai_detections(project_id, scene_id);
CREATE INDEX IF NOT EXISTS idx_ai_det_label ON ai_detections(label);
CREATE INDEX IF NOT EXISTS idx_ai_det_geom_gix ON ai_detections USING GIST (geom);

-- 7) 差异结果
CREATE TABLE IF NOT EXISTS diff_results (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  scene_id UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
  registration_result_id UUID REFERENCES registration_results(id) ON DELETE SET NULL,
  diff_type VARCHAR(32) NOT NULL, -- geometry/semantic/rule
  object_id UUID,
  object_type VARCHAR(64),
  metric_name VARCHAR(64),
  metric_value NUMERIC(12,5),
  threshold_value NUMERIC(12,5),
  risk_level VARCHAR(16),
  status VARCHAR(32) NOT NULL DEFAULT 'new', -- new/confirmed/rejected
  geom GEOMETRY(Geometry, 4490),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  reviewed_by VARCHAR(128),
  reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_diff_project_scene ON diff_results(project_id, scene_id);
CREATE INDEX IF NOT EXISTS idx_diff_type_level ON diff_results(diff_type, risk_level);
CREATE INDEX IF NOT EXISTS idx_diff_geom_gix ON diff_results USING GIST (geom);

-- 8) 规则与风险事件
CREATE TABLE IF NOT EXISTS risk_rulesets (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  name VARCHAR(255) NOT NULL,
  version VARCHAR(64) NOT NULL,
  content JSONB NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT false,
  created_by VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ruleset_project_active ON risk_rulesets(project_id, is_active);

CREATE TABLE IF NOT EXISTS risk_events (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  scene_id UUID NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
  source_diff_id UUID REFERENCES diff_results(id) ON DELETE SET NULL,
  source_ai_id UUID REFERENCES ai_detections(id) ON DELETE SET NULL,
  rule_id VARCHAR(128),
  risk_type VARCHAR(128) NOT NULL,
  level VARCHAR(16) NOT NULL,
  title VARCHAR(255),
  description TEXT,
  suggestion TEXT,
  location GEOMETRY(Point, 4490),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR(32) NOT NULL DEFAULT 'open', -- open/in_progress/resolved/closed
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_risk_project_scene ON risk_events(project_id, scene_id);
CREATE INDEX IF NOT EXISTS idx_risk_level_status ON risk_events(level, status);
CREATE INDEX IF NOT EXISTS idx_risk_loc_gix ON risk_events USING GIST (location);

-- 9) 工单闭环
CREATE TABLE IF NOT EXISTS work_orders (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  event_id UUID NOT NULL REFERENCES risk_events(id) ON DELETE CASCADE,
  assignee VARCHAR(128),
  status VARCHAR(32) NOT NULL DEFAULT 'open', -- open/accepted/resolved/reopened/closed
  deadline TIMESTAMPTZ,
  resolution_note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_workorder_event ON work_orders(event_id);
CREATE INDEX IF NOT EXISTS idx_workorder_status ON work_orders(status);

CREATE TABLE IF NOT EXISTS work_order_logs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  work_order_id UUID NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
  action VARCHAR(64) NOT NULL,
  operator VARCHAR(128),
  comment TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_workorder_logs_wo ON work_order_logs(work_order_id);

-- 10) 外部大模型调用审计（网关层）
CREATE TABLE IF NOT EXISTS llm_calls (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
  scene_id UUID REFERENCES scenes(id) ON DELETE SET NULL,
  event_id UUID REFERENCES risk_events(id) ON DELETE SET NULL,
  provider VARCHAR(64) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  endpoint_alias VARCHAR(128),
  prompt_template_id VARCHAR(128),
  input_tokens INT,
  output_tokens INT,
  latency_ms INT,
  status VARCHAR(32) NOT NULL, -- success/failed/fallback
  request_hash VARCHAR(128),
  response_summary TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_project_scene ON llm_calls(project_id, scene_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_status ON llm_calls(status);

-- 11) 触发器：updated_at自动维护（可选）
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_projects_updated_at ON projects;
CREATE TRIGGER trg_projects_updated_at
BEFORE UPDATE ON projects
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_scenes_updated_at ON scenes;
CREATE TRIGGER trg_scenes_updated_at
BEFORE UPDATE ON scenes
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_risk_events_updated_at ON risk_events;
CREATE TRIGGER trg_risk_events_updated_at
BEFORE UPDATE ON risk_events
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_work_orders_updated_at ON work_orders;
CREATE TRIGGER trg_work_orders_updated_at
BEFORE UPDATE ON work_orders
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
