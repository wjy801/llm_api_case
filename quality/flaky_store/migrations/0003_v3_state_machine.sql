ALTER TABLE flaky_import_run
ADD COLUMN run_kind TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN'
CHECK (run_kind IN ('NORMAL', 'FLAKY_PROBE', 'LEGACY_UNKNOWN'));

ALTER TABLE flaky_import_run ADD COLUMN policy_revision TEXT;
ALTER TABLE flaky_import_run ADD COLUMN controller_commit_sha TEXT;
ALTER TABLE flaky_import_run ADD COLUMN attempt_id TEXT;
ALTER TABLE flaky_import_run ADD COLUMN trigger_id TEXT;
ALTER TABLE flaky_import_run ADD COLUMN plan_digest TEXT;
ALTER TABLE flaky_import_run ADD COLUMN round_no INTEGER CHECK (round_no IS NULL OR round_no >= 1);
ALTER TABLE flaky_import_run ADD COLUMN target_commit_sha TEXT;
ALTER TABLE flaky_import_run ADD COLUMN jenkins_job_name TEXT;
ALTER TABLE flaky_import_run ADD COLUMN jenkins_build_number TEXT;
ALTER TABLE flaky_import_run ADD COLUMN fact_schema_version TEXT;
ALTER TABLE flaky_import_run ADD COLUMN plugin_version TEXT;
ALTER TABLE flaky_import_run
ADD COLUMN legacy_record INTEGER NOT NULL DEFAULT 1
CHECK (legacy_record IN (0, 1));

CREATE TABLE flaky_identity (
    flaky_key TEXT PRIMARY KEY,
    epoch_scope_key TEXT NOT NULL,
    case_id TEXT NOT NULL,
    param_hash TEXT NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('china', 'overseas')),
    execution_profile TEXT NOT NULL,
    state_epoch INTEGER NOT NULL CHECK (state_epoch >= 1),
    current_detection_generation INTEGER NOT NULL DEFAULT 1
        CHECK (current_detection_generation >= 1),
    legacy_detected_state TEXT CHECK (
        legacy_detected_state IS NULL OR legacy_detected_state IN (
            'OBSERVING', 'STABLE', 'SUSPECTED', 'CONFIRMED'
        )
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (epoch_scope_key) REFERENCES flaky_case_epoch(epoch_scope_key),
    UNIQUE (case_id, param_hash, environment, execution_profile, state_epoch)
);

INSERT INTO flaky_identity (
    flaky_key, epoch_scope_key, case_id, param_hash, environment,
    execution_profile, state_epoch, current_detection_generation,
    legacy_detected_state, created_at, updated_at
)
SELECT
    identity_source.flaky_key,
    MIN(identity_source.epoch_scope_key),
    MIN(identity_source.case_id),
    MIN(identity_source.param_hash),
    MIN(identity_source.environment),
    MIN(identity_source.execution_profile),
    MIN(identity_source.state_epoch),
    1,
    MAX(identity_source.legacy_detected_state),
    MIN(identity_source.created_at),
    MAX(identity_source.updated_at)
FROM (
    SELECT
        observation.flaky_key,
        observation.epoch_scope_key,
        observation.case_id,
        observation.param_hash,
        observation.environment,
        observation.execution_profile,
        observation.state_epoch,
        NULL AS legacy_detected_state,
        observation.observed_at AS created_at,
        observation.observed_at AS updated_at
    FROM case_observation AS observation
    UNION ALL
    SELECT
        state.flaky_key,
        state.epoch_scope_key,
        state.case_id,
        state.param_hash,
        state.environment,
        state.execution_profile,
        state.state_epoch,
        state.detected_state AS legacy_detected_state,
        state.created_at,
        state.updated_at
    FROM flaky_state AS state
) AS identity_source
GROUP BY identity_source.flaky_key;

CREATE TABLE flaky_evidence_admission (
    admission_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('RUN', 'CASE')),
    case_key TEXT NOT NULL,
    flaky_key TEXT,
    status TEXT NOT NULL CHECK (status IN ('ELIGIBLE', 'INELIGIBLE')),
    primary_reason_code TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json)),
    policy_revision TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES flaky_import_run(run_id),
    UNIQUE (run_id, scope, case_key)
);

CREATE TABLE flaky_normal_observation (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    flaky_key TEXT NOT NULL,
    detection_generation INTEGER NOT NULL CHECK (detection_generation >= 1),
    comparability_fingerprint TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('pass', 'fail')),
    failure_fingerprint TEXT,
    observed_at TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    admission_rule_version TEXT NOT NULL,
    detection_rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES flaky_import_run(run_id),
    FOREIGN KEY (flaky_key) REFERENCES flaky_identity(flaky_key),
    UNIQUE (run_id, flaky_key),
    CHECK (
        (outcome = 'pass' AND failure_fingerprint IS NULL)
        OR (outcome = 'fail' AND failure_fingerprint IS NOT NULL)
    )
);

CREATE INDEX idx_flaky_normal_observation_cohort
ON flaky_normal_observation(
    flaky_key, detection_generation, comparability_fingerprint,
    observed_at, run_id, observation_id
);

CREATE TABLE flaky_detection_projection (
    flaky_key TEXT NOT NULL,
    detection_generation INTEGER NOT NULL CHECK (detection_generation >= 1),
    comparability_fingerprint TEXT NOT NULL,
    detection_state TEXT NOT NULL CHECK (
        detection_state IN ('OBSERVING', 'STABLE', 'SUSPECTED', 'CONFIRMED')
    ),
    sample_size INTEGER NOT NULL CHECK (sample_size >= 1),
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
    stable_outcome TEXT CHECK (stable_outcome IN ('pass', 'fail')),
    stable_failure_fingerprint TEXT,
    latest_observation_id TEXT NOT NULL,
    last_transition_id TEXT,
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (flaky_key, detection_generation, comparability_fingerprint),
    FOREIGN KEY (flaky_key) REFERENCES flaky_identity(flaky_key),
    FOREIGN KEY (latest_observation_id)
        REFERENCES flaky_normal_observation(observation_id),
    CHECK (
        (stable_outcome = 'pass' AND stable_failure_fingerprint IS NULL)
        OR (stable_outcome = 'fail' AND stable_failure_fingerprint IS NOT NULL)
        OR (stable_outcome IS NULL AND stable_failure_fingerprint IS NULL)
    )
);

CREATE TABLE flaky_detection_transition (
    transition_id TEXT PRIMARY KEY,
    flaky_key TEXT NOT NULL,
    detection_generation INTEGER NOT NULL CHECK (detection_generation >= 1),
    comparability_fingerprint TEXT NOT NULL,
    from_state TEXT CHECK (
        from_state IS NULL OR from_state IN (
            'OBSERVING', 'STABLE', 'SUSPECTED', 'CONFIRMED'
        )
    ),
    to_state TEXT NOT NULL CHECK (
        to_state IN ('OBSERVING', 'STABLE', 'SUSPECTED', 'CONFIRMED')
    ),
    reason_code TEXT NOT NULL,
    transition_version TEXT NOT NULL CHECK (
        transition_version IN ('transition-v1', 'transition-v2')
    ),
    trigger_observation_id TEXT,
    override_id TEXT,
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (flaky_key, detection_generation, comparability_fingerprint)
        REFERENCES flaky_detection_projection(
            flaky_key, detection_generation, comparability_fingerprint
        ),
    FOREIGN KEY (trigger_observation_id)
        REFERENCES flaky_normal_observation(observation_id),
    CHECK (
        (transition_version = 'transition-v1' AND override_id IS NULL)
        OR (transition_version = 'transition-v2' AND override_id IS NOT NULL)
    )
);

CREATE INDEX idx_flaky_detection_transition_cohort
ON flaky_detection_transition(
    flaky_key, detection_generation, comparability_fingerprint,
    created_at, transition_id
);

CREATE TABLE flaky_detection_override (
    override_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    flaky_key TEXT NOT NULL,
    detection_generation INTEGER NOT NULL CHECK (detection_generation >= 1),
    comparability_fingerprint TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('confirm_flaky', 'mark_not_flaky')),
    from_state TEXT NOT NULL CHECK (
        from_state IN ('OBSERVING', 'STABLE', 'SUSPECTED', 'CONFIRMED')
    ),
    to_state TEXT NOT NULL CHECK (
        to_state IN ('OBSERVING', 'STABLE', 'SUSPECTED', 'CONFIRMED')
    ),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (flaky_key, detection_generation, comparability_fingerprint)
        REFERENCES flaky_detection_projection(
            flaky_key, detection_generation, comparability_fingerprint
        )
);

DROP INDEX idx_flaky_governance_one_open;
DROP INDEX idx_flaky_governance_status_expiry;
ALTER TABLE flaky_governance RENAME TO flaky_governance_v2;

CREATE TABLE flaky_governance (
    governance_id TEXT PRIMARY KEY,
    flaky_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RECOVERING', 'CLOSED')),
    owner TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    recovery_started_by TEXT,
    recovery_started_at TEXT,
    recovery_reason TEXT,
    closed_at TEXT,
    closed_by TEXT,
    close_reason TEXT,
    close_attempt_id TEXT,
    resolution TEXT CHECK (resolution IN ('recovered', 'regressed', 'cancelled')),
    legacy_governance INTEGER NOT NULL DEFAULT 0 CHECK (legacy_governance IN (0, 1)),
    FOREIGN KEY (flaky_key) REFERENCES flaky_identity(flaky_key),
    CHECK (expires_at > created_at),
    CHECK (
        (status = 'RECOVERING'
            AND recovery_started_by IS NOT NULL
            AND recovery_started_at IS NOT NULL
            AND recovery_reason IS NOT NULL)
        OR status != 'RECOVERING'
    ),
    CHECK (
        (status = 'CLOSED'
            AND closed_at IS NOT NULL
            AND closed_by IS NOT NULL
            AND close_reason IS NOT NULL
            AND resolution IS NOT NULL)
        OR (status != 'CLOSED'
            AND closed_at IS NULL
            AND closed_by IS NULL
            AND close_reason IS NULL
            AND close_attempt_id IS NULL
            AND resolution IS NULL)
    )
);

INSERT INTO flaky_governance (
    governance_id, flaky_key, status, owner, reason, created_by,
    created_at, expires_at, row_version, recovery_started_by,
    recovery_started_at, recovery_reason, closed_at, closed_by,
    close_reason, close_attempt_id, resolution, legacy_governance
)
SELECT
    governance_id,
    flaky_key,
    CASE WHEN status = 'RECOVERING' THEN 'ACTIVE' ELSE status END,
    owner,
    reason,
    created_by,
    created_at,
    expires_at,
    1,
    NULL,
    NULL,
    NULL,
    closed_at,
    CASE WHEN status = 'CLOSED' THEN COALESCE(recovery_started_by, created_by) END,
    CASE WHEN status = 'CLOSED' THEN 'legacy_v2_' || resolution END,
    NULL,
    resolution,
    1
FROM flaky_governance_v2;

CREATE UNIQUE INDEX idx_flaky_governance_one_open
ON flaky_governance(flaky_key)
WHERE status IN ('ACTIVE', 'RECOVERING');

CREATE INDEX idx_flaky_governance_status_expiry
ON flaky_governance(status, expires_at);

CREATE TABLE flaky_verification_attempt (
    attempt_id TEXT PRIMARY KEY,
    governance_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
    status TEXT NOT NULL CHECK (
        status IN (
            'ACTIVE', 'READY_TO_CLOSE', 'FAILED', 'INCONCLUSIVE',
            'EXPIRED', 'CANCELLED', 'CLOSED'
        )
    ),
    target_commit_sha TEXT NOT NULL CHECK (
        length(target_commit_sha) = 40 AND target_commit_sha NOT GLOB '*[^0-9a-f]*'
    ),
    policy_revision TEXT NOT NULL,
    required_consecutive_passes INTEGER NOT NULL CHECK (required_consecutive_passes >= 1),
    min_interval_minutes INTEGER NOT NULL CHECK (min_interval_minutes >= 0),
    max_non_counting_runs INTEGER NOT NULL CHECK (max_non_counting_runs >= 1),
    counted_passes INTEGER NOT NULL DEFAULT 0 CHECK (counted_passes >= 0),
    non_counting_runs INTEGER NOT NULL DEFAULT 0 CHECK (non_counting_runs >= 0),
    started_by TEXT NOT NULL,
    start_reason TEXT NOT NULL,
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (governance_id) REFERENCES flaky_governance(governance_id),
    UNIQUE (governance_id, attempt_no),
    CHECK (expires_at > started_at),
    CHECK (
        (status IN ('ACTIVE', 'READY_TO_CLOSE') AND ended_at IS NULL AND end_reason IS NULL)
        OR (status NOT IN ('ACTIVE', 'READY_TO_CLOSE')
            AND ended_at IS NOT NULL AND end_reason IS NOT NULL)
    )
);

CREATE UNIQUE INDEX idx_flaky_attempt_one_live
ON flaky_verification_attempt(governance_id)
WHERE status IN ('ACTIVE', 'READY_TO_CLOSE');

CREATE TABLE flaky_probe_trigger (
    trigger_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    plan_digest TEXT NOT NULL,
    target_commit_sha TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'EVIDENCE_COMPLETE', 'CANCELLED')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES flaky_verification_attempt(attempt_id)
);

CREATE TABLE flaky_probe_evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL,
    trigger_id TEXT NOT NULL,
    reported_trigger_id TEXT NOT NULL,
    round_no INTEGER NOT NULL CHECK (round_no >= 1),
    trusted_started_at TEXT NOT NULL,
    raw_outcome TEXT NOT NULL CHECK (
        raw_outcome IN ('PASS', 'FAIL', 'SKIP', 'XFAIL', 'XPASS', 'NO_DATA')
    ),
    p0_trusted INTEGER NOT NULL CHECK (p0_trusted IN (0, 1)),
    rerun_supported INTEGER NOT NULL CHECK (rerun_supported IN (0, 1)),
    trusted_failure INTEGER NOT NULL CHECK (trusted_failure IN (0, 1)),
    plan_matches INTEGER NOT NULL CHECK (plan_matches IN (0, 1)),
    arrived_after_terminal INTEGER NOT NULL CHECK (arrived_after_terminal IN (0, 1)),
    classification TEXT NOT NULL CHECK (
        classification IN ('COUNT_PASS', 'TRUSTED_FAIL', 'NON_COUNTING')
    ),
    primary_reason_code TEXT NOT NULL,
    diagnostic_codes_json TEXT NOT NULL CHECK (json_valid(diagnostic_codes_json)),
    consumes_non_counting_quota INTEGER NOT NULL
        CHECK (consumes_non_counting_quota IN (0, 1)),
    effect_status TEXT NOT NULL CHECK (effect_status IN ('APPLIED', 'AUDIT_ONLY')),
    admission_rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES flaky_import_run(run_id),
    FOREIGN KEY (attempt_id) REFERENCES flaky_verification_attempt(attempt_id),
    FOREIGN KEY (trigger_id) REFERENCES flaky_probe_trigger(trigger_id)
);

CREATE UNIQUE INDEX idx_flaky_probe_one_applied_round
ON flaky_probe_evidence(attempt_id, round_no)
WHERE effect_status = 'APPLIED';

CREATE INDEX idx_flaky_probe_attempt_order
ON flaky_probe_evidence(attempt_id, round_no, trusted_started_at, run_id);

CREATE TABLE flaky_governance_event (
    event_id TEXT PRIMARY KEY,
    governance_id TEXT NOT NULL,
    attempt_id TEXT,
    event_type TEXT NOT NULL,
    causal_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (governance_id) REFERENCES flaky_governance(governance_id),
    FOREIGN KEY (attempt_id) REFERENCES flaky_verification_attempt(attempt_id),
    UNIQUE (governance_id, event_type, causal_id)
);

INSERT INTO flaky_governance_event (
    event_id, governance_id, attempt_id, event_type, causal_id,
    from_status, to_status, actor, reason, created_at
)
SELECT
    'governance-event-v1-legacy-recovery-' || governance_id,
    governance_id,
    NULL,
    'legacy_recovery_requires_new_attempt',
    governance_id,
    'RECOVERING',
    'ACTIVE',
    COALESCE(recovery_started_by, created_by),
    'legacy v2 recovery cannot be continued without a Probe attempt',
    COALESCE(recovery_started_at, created_at)
FROM flaky_governance_v2
WHERE status = 'RECOVERING';

DROP TABLE flaky_governance_v2;
