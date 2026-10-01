CREATE TABLE replan_jobs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    based_on_version BIGINT NOT NULL CHECK (based_on_version >= 0),
    based_on_time_s BIGINT NOT NULL CHECK (based_on_time_s >= 0),
    status TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed','superseded')),
    result JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX jobs_run ON replan_jobs(run_id,created_at);
ALTER TABLE replan_requests ADD COLUMN job_id TEXT REFERENCES replan_jobs(id);
-- The last event at one model instant carries the committed dynamic projection.
-- Historical reads replay these projections after the nearest periodic snapshot.
ALTER TABLE events ADD COLUMN projection JSONB;
ALTER TABLE runs ADD COLUMN replan_required BOOLEAN NOT NULL DEFAULT false;
UPDATE runs SET replan_required=true WHERE id IN (SELECT run_id FROM replan_requests);
