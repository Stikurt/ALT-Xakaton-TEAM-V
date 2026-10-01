-- Durable latest-request slot. A batch produces a single request, not N jobs.
-- The calculation worker consumes it in stage 4; this row is not a running job.
CREATE TABLE replan_requests (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    based_on_version BIGINT NOT NULL CHECK (based_on_version >= 0),
    last_seq BIGINT NOT NULL CHECK (last_seq >= 0),
    command_id TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
