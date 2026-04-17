-- migration_down.sql
-- 水利工程 CAD对比实景系统数据库迁移回滚（DOWN）

BEGIN;

-- 先删除触发器
DROP TRIGGER IF EXISTS trg_work_orders_updated_at ON work_orders;
DROP TRIGGER IF EXISTS trg_risk_events_updated_at ON risk_events;
DROP TRIGGER IF EXISTS trg_scenes_updated_at ON scenes;
DROP TRIGGER IF EXISTS trg_projects_updated_at ON projects;

-- 删除函数
DROP FUNCTION IF EXISTS set_updated_at();

-- 按依赖逆序删除表
DROP TABLE IF EXISTS llm_calls;
DROP TABLE IF EXISTS work_order_logs;
DROP TABLE IF EXISTS work_orders;
DROP TABLE IF EXISTS risk_events;
DROP TABLE IF EXISTS risk_rulesets;
DROP TABLE IF EXISTS diff_results;
DROP TABLE IF EXISTS ai_detections;
DROP TABLE IF EXISTS registration_results;
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS design_objects;
DROP TABLE IF EXISTS models;
DROP TABLE IF EXISTS assets;
DROP TABLE IF EXISTS scenes;
DROP TABLE IF EXISTS projects;

-- 说明：通常不在down中删除扩展，避免影响同库其他业务
-- DROP EXTENSION IF EXISTS postgis;
-- DROP EXTENSION IF EXISTS "uuid-ossp";

COMMIT;
