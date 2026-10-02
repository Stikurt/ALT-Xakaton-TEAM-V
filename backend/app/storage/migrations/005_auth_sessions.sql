-- Stage 6: server-side sessions. The cookie carries an opaque random token;
-- only HMAC-SHA256(SESSION_SECRET, token) is stored, never the token itself.
CREATE TABLE auth_sessions (
    id TEXT PRIMARY KEY,
    token_hash BYTEA NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    username TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'dispatcher', 'admin')),
    -- Changes when the user's configured password hash changes: old sessions stop working.
    credential_tag TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CHECK (expires_at > created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX auth_sessions_expiry ON auth_sessions(expires_at);
CREATE INDEX auth_sessions_active_user ON auth_sessions(username) WHERE revoked_at IS NULL;
