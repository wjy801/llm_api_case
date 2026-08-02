CREATE TABLE flaky_state (
    flaky_key TEXT PRIMARY KEY,
    epoch_scope_key TEXT NOT NULL,
    case_id TEXT NOT NULL,
    param_hash TEXT NOT NULL,
    environment TEXT NOT NULL,
    execution_profile TEXT NOT NULL,
    state_epoch INTEGER NOT NULL CHECK (state_epoch >= 1),
    current_state TEXT NOT NULL CHECK (
        current_state IN (
            'OBSERVING', 'STABLE', 'SUSPECTED',
            'CONFIRMED', 'QUARANTINED', 'RECOVERING'
        )
    ),
    detected_state TEXT NOT NULL CHECK (
        detected_state IN ('OBSERVING', 'STABLE', 'SUSPECTED', 'CONFIRMED')
    ),
    stable_outcome TEXT CHECK (stable_outcome IN ('pass', 'fail')),
    stable_failure_id TEXT,
    total_observation_count INTEGER NOT NULL CHECK (total_observation_count >= 1),
    sample_size INTEGER NOT NULL CHECK (sample_size >= 1),
    evidence_window_size INTEGER NOT NULL CHECK (evidence_window_size >= sample_size),
    pass_count INTEGER NOT NULL CHECK (pass_count >= 0),
    fail_count INTEGER NOT NULL CHECK (fail_count >= 0),
    outcome_switch_count INTEGER NOT NULL CHECK (outcome_switch_count >= 0),
    signature_switch_count INTEGER NOT NULL CHECK (signature_switch_count >= 0),
    distinct_failure_fingerprint_count INTEGER NOT NULL CHECK (
        distinct_failure_fingerprint_count >= 0
    ),
    trailing_same_signature_count INTEGER NOT NULL CHECK (
        trailing_same_signature_count >= 1
    ),
    evaluation_anchor_observation_id TEXT,
    latest_observation_id TEXT NOT NULL,
    latest_run_id TEXT NOT NULL,
    latest_observed_at TEXT NOT NULL,
    last_transition_id TEXT,
    rule_version TEXT NOT NULL,
    projection_version TEXT NOT NULL,
    projection_status TEXT NOT NULL CHECK (
        projection_status IN ('CURRENT', 'STALE')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (epoch_scope_key) REFERENCES flaky_case_epoch(epoch_scope_key),
    FOREIGN KEY (latest_observation_id) REFERENCES case_observation(observation_id),
    FOREIGN KEY (evaluation_anchor_observation_id) REFERENCES case_observation(observation_id),
    UNIQUE (case_id, param_hash, environment, execution_profile, state_epoch),
    CHECK (
        (stable_outcome = 'pass' AND stable_failure_id IS NULL)
        OR stable_outcome = 'fail'
        OR stable_outcome IS NULL
    )
);

CREATE INDEX idx_flaky_state_status_updated
ON flaky_state(current_state, updated_at);

CREATE INDEX idx_flaky_state_case_lookup
ON flaky_state(case_id, environment, execution_profile, state_epoch);

CREATE TABLE flaky_transition (
    transition_id TEXT PRIMARY KEY,
    flaky_key TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK (
        trigger_type IN ('observation', 'manual', 'bootstrap', 'reprojection')
    ),
    reason_code TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    projection_version TEXT NOT NULL,
    sample_size INTEGER NOT NULL CHECK (sample_size >= 1),
    trigger_observation_id TEXT,
    evidence_observation_ids_json TEXT NOT NULL,
    evidence_run_ids_json TEXT NOT NULL,
    actor TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (flaky_key) REFERENCES flaky_state(flaky_key),
    FOREIGN KEY (trigger_observation_id) REFERENCES case_observation(observation_id)
);

CREATE INDEX idx_flaky_transition_key_time
ON flaky_transition(flaky_key, created_at, transition_id);

CREATE INDEX idx_flaky_transition_target_time
ON flaky_transition(to_state, created_at);

CREATE TABLE flaky_governance (
    governance_id TEXT PRIMARY KEY,
    flaky_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RECOVERING', 'CLOSED')),
    owner TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    recovery_started_by TEXT,
    recovery_started_at TEXT,
    recovery_reason TEXT,
    recovery_anchor_observation_id TEXT,
    closed_at TEXT,
    resolution TEXT CHECK (resolution IN ('recovered', 'regressed', 'cancelled')),
    FOREIGN KEY (flaky_key) REFERENCES flaky_state(flaky_key),
    FOREIGN KEY (recovery_anchor_observation_id)
        REFERENCES case_observation(observation_id),
    CHECK (expires_at > created_at),
    CHECK (
        status != 'RECOVERING'
        OR (
            recovery_started_by IS NOT NULL
            AND recovery_started_at IS NOT NULL
            AND recovery_reason IS NOT NULL
        )
    ),
    CHECK (
        status != 'CLOSED'
        OR (closed_at IS NOT NULL AND resolution IS NOT NULL)
    )
);

CREATE UNIQUE INDEX idx_flaky_governance_one_open
ON flaky_governance(flaky_key)
WHERE status IN ('ACTIVE', 'RECOVERING');

CREATE INDEX idx_flaky_governance_status_expiry
ON flaky_governance(status, expires_at);

DROP INDEX idx_flaky_override_scope_time;
ALTER TABLE flaky_override RENAME TO flaky_override_v1;

CREATE TABLE flaky_override (
    override_id TEXT PRIMARY KEY,
    epoch_scope_key TEXT NOT NULL,
    flaky_key TEXT,
    action TEXT NOT NULL CHECK (
        action IN (
            'reset_epoch', 'confirm_flaky',
            'mark_not_flaky', 'cancel_quarantine'
        )
    ),
    previous_epoch INTEGER,
    new_epoch INTEGER,
    from_state TEXT,
    to_state TEXT,
    trigger_observation_id TEXT,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (epoch_scope_key) REFERENCES flaky_case_epoch(epoch_scope_key),
    FOREIGN KEY (flaky_key) REFERENCES flaky_state(flaky_key),
    FOREIGN KEY (trigger_observation_id)
        REFERENCES case_observation(observation_id),
    CHECK (
        (
            action = 'reset_epoch'
            AND previous_epoch >= 1
            AND new_epoch = previous_epoch + 1
            AND flaky_key IS NULL
            AND from_state IS NULL
            AND to_state IS NULL
        )
        OR
        (
            action != 'reset_epoch'
            AND previous_epoch IS NULL
            AND new_epoch IS NULL
            AND flaky_key IS NOT NULL
            AND from_state IS NOT NULL
            AND to_state IS NOT NULL
        )
    )
);

INSERT INTO flaky_override (
    override_id, epoch_scope_key, flaky_key, action,
    previous_epoch, new_epoch, from_state, to_state,
    trigger_observation_id, actor, reason, created_at
)
SELECT
    override_id, epoch_scope_key, NULL, action,
    previous_epoch, new_epoch, NULL, NULL,
    NULL, actor, reason, created_at
FROM flaky_override_v1;

DROP TABLE flaky_override_v1;

CREATE INDEX idx_flaky_override_scope_time
ON flaky_override(epoch_scope_key, created_at);

CREATE INDEX idx_flaky_override_key_time
ON flaky_override(flaky_key, created_at);
