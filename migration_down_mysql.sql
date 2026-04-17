-- migration_down_mysql.sql
-- MySQL 8 迁移回滚（DOWN）

SET FOREIGN_KEY_CHECKS = 0;

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

SET FOREIGN_KEY_CHECKS = 1;
