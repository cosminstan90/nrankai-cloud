-- Run this on the VPS to add the n8n_notified column to the existing database.
-- SQLite supports ADD COLUMN for nullable / default columns without a full rebuild.
--
-- Usage (on VPS):
--   cd /opt/stancosmin_cloud
--   sqlite3 lead_audits.db < migrate_add_n8n_notified.sql

ALTER TABLE lead_audit_jobs ADD COLUMN n8n_notified INTEGER NOT NULL DEFAULT 0;

-- Verify
SELECT COUNT(*) AS total, SUM(n8n_notified) AS notified FROM lead_audit_jobs;
