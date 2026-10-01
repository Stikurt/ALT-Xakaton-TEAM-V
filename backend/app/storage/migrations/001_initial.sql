CREATE TABLE station_config (
    id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    config JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    station_config_id TEXT NOT NULL REFERENCES station_config(id),
    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    last_seq BIGINT NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_run ON runs(status) WHERE status = 'active';

CREATE TABLE events (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    seq BIGINT NOT NULL CHECK (seq > 0),
    sim_time_s BIGINT NOT NULL CHECK (sim_time_s >= 0),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    type TEXT NOT NULL,
    entity_id TEXT,
    payload JSONB NOT NULL,
    PRIMARY KEY (run_id, event_id),
    UNIQUE (run_id, seq)
);
CREATE INDEX events_history ON events(run_id, sim_time_s, seq);

CREATE TABLE snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    state_version BIGINT NOT NULL CHECK (state_version >= 0),
    last_seq BIGINT NOT NULL CHECK (last_seq >= 0),
    sim_time_s BIGINT NOT NULL CHECK (sim_time_s >= 0),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, sim_time_s, last_seq)
);
CREATE INDEX snapshots_history ON snapshots(run_id, sim_time_s DESC, last_seq DESC);

CREATE TABLE plans (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    based_on_version BIGINT NOT NULL CHECK (based_on_version >= 0),
    status TEXT NOT NULL CHECK (status IN ('feasible', 'partial', 'infeasible', 'timeout')),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ,
    PRIMARY KEY (run_id, id)
);
CREATE INDEX plans_created ON plans(run_id, created_at);

CREATE TABLE operation_actuals (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL,
    train_id TEXT NOT NULL,
    actual_start_s BIGINT NOT NULL CHECK (actual_start_s >= 0),
    actual_end_s BIGINT CHECK (actual_end_s > actual_start_s),
    PRIMARY KEY (run_id, operation_id)
);

-- Completed command results must be written in the same transaction as their events.
CREATE TABLE commands (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    command_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_status INTEGER NOT NULL CHECK (response_status BETWEEN 200 AND 599),
    response JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, command_id)
);
