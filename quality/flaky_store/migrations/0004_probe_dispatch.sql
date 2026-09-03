ALTER TABLE flaky_probe_evidence RENAME TO flaky_probe_evidence_v3;
ALTER TABLE flaky_probe_trigger RENAME TO flaky_probe_trigger_v3;

DROP INDEX idx_flaky_probe_one_applied_round;
DROP INDEX idx_flaky_probe_attempt_order;

CREATE TABLE flaky_probe_plan (
    attempt_id TEXT PRIMARY KEY,
    governance_id TEXT NOT NULL,
    flaky_key TEXT NOT NULL,
    plan_version TEXT NOT NULL,
    canonical_json TEXT NOT NULL CHECK (json_valid(canonical_json)),
    plan_digest TEXT NOT NULL UNIQUE CHECK (
        length(plan_digest) = 71 AND plan_digest GLOB 'sha256:*'
    ),
    case_id TEXT NOT NULL,
    param_hash TEXT NOT NULL,
    environment TEXT NOT NULL,
    execution_profile TEXT NOT NULL,
    state_epoch INTEGER NOT NULL CHECK (state_epoch >= 1),
    target_branch TEXT NOT NULL,
    target_commit_sha TEXT NOT NULL CHECK (
        length(target_commit_sha) = 40
        AND target_commit_sha NOT GLOB '*[^0-9a-f]*'
    ),
    controller_commit_sha TEXT NOT NULL CHECK (
        length(controller_commit_sha) = 40
        AND controller_commit_sha NOT GLOB '*[^0-9a-f]*'
    ),
    policy_revision TEXT NOT NULL,
    probe_evidence_rule_version TEXT NOT NULL,
    fact_schema_version TEXT NOT NULL,
    required_consecutive_passes INTEGER NOT NULL CHECK (required_consecutive_passes >= 1),
    min_interval_minutes INTEGER NOT NULL CHECK (min_interval_minutes >= 0),
    max_attempt_age_hours INTEGER NOT NULL CHECK (max_attempt_age_hours >= 1),
    max_non_counting_runs INTEGER NOT NULL CHECK (max_non_counting_runs >= 1),
    max_dispatch_attempts INTEGER NOT NULL CHECK (max_dispatch_attempts >= 1),
    max_orchestration_rounds INTEGER NOT NULL CHECK (max_orchestration_rounds >= 1),
    allowed_job_full_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES flaky_verification_attempt(attempt_id),
    FOREIGN KEY (governance_id) REFERENCES flaky_governance(governance_id),
    FOREIGN KEY (flaky_key) REFERENCES flaky_identity(flaky_key)
);

CREATE TRIGGER flaky_probe_plan_no_update
BEFORE UPDATE ON flaky_probe_plan
BEGIN
    SELECT RAISE(ABORT, 'flaky_probe_plan is immutable');
END;

CREATE TRIGGER flaky_probe_plan_no_delete
BEFORE DELETE ON flaky_probe_plan
BEGIN
    SELECT RAISE(ABORT, 'flaky_probe_plan is immutable');
END;

CREATE TABLE flaky_probe_trigger (
    trigger_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL CHECK (
        length(payload_hash) = 71 AND payload_hash GLOB 'sha256:*'
    ),
    plan_digest TEXT NOT NULL,
    target_commit_sha TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING', 'DISPATCHING', 'QUEUED', 'DISPATCH_UNKNOWN',
            'RUNNING', 'CANCEL_REQUESTED', 'COMPLETED', 'FAILED', 'CANCELLED'
        )
    ),
    failure_disposition TEXT CHECK (
        failure_disposition IS NULL
        OR failure_disposition IN ('RETRYABLE', 'TERMINAL')
    ),
    dispatch_attempt_no INTEGER NOT NULL DEFAULT 0 CHECK (dispatch_attempt_no >= 0),
    token_hash TEXT CHECK (
        token_hash IS NULL
        OR (length(token_hash) = 71 AND token_hash GLOB 'sha256:*')
    ),
    claimed_token_hash TEXT CHECK (
        claimed_token_hash IS NULL
        OR (length(claimed_token_hash) = 71 AND claimed_token_hash GLOB 'sha256:*')
    ),
    allowed_job_full_name TEXT NOT NULL,
    jenkins_queue_id INTEGER CHECK (jenkins_queue_id IS NULL OR jenkins_queue_id >= 1),
    claimed_job_full_name TEXT,
    claimed_build_number INTEGER CHECK (
        claimed_build_number IS NULL OR claimed_build_number >= 1
    ),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    dispatch_started_at TEXT,
    queued_at TEXT,
    claimed_at TEXT,
    cancel_requested_at TEXT,
    terminal_at TEXT,
    next_reconcile_at TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES flaky_verification_attempt(attempt_id),
    FOREIGN KEY (plan_digest) REFERENCES flaky_probe_plan(plan_digest),
    CHECK (
        (status = 'FAILED' AND failure_disposition IS NOT NULL)
        OR (status != 'FAILED' AND failure_disposition IS NULL)
    ),
    CHECK (
        (status = 'RUNNING' AND claimed_build_number IS NOT NULL
         AND claimed_job_full_name IS NOT NULL AND claimed_at IS NOT NULL)
        OR status != 'RUNNING'
    ),
    CHECK (status != 'QUEUED' OR jenkins_queue_id IS NOT NULL)
);

CREATE INDEX idx_flaky_probe_trigger_dispatch
ON flaky_probe_trigger(status, updated_at, trigger_id);

CREATE UNIQUE INDEX idx_flaky_probe_claimed_build
ON flaky_probe_trigger(claimed_job_full_name, claimed_build_number)
WHERE claimed_build_number IS NOT NULL;

CREATE TABLE flaky_probe_round (
    attempt_id TEXT NOT NULL,
    round_no INTEGER NOT NULL CHECK (round_no >= 1),
    status TEXT NOT NULL CHECK (
        status IN ('AUTHORIZED', 'STARTED', 'IMPORTED', 'ABANDONED')
    ),
    run_id TEXT NOT NULL UNIQUE,
    actual_target_commit_sha TEXT CHECK (
        actual_target_commit_sha IS NULL
        OR (
            length(actual_target_commit_sha) = 40
            AND actual_target_commit_sha NOT GLOB '*[^0-9a-f]*'
        )
    ),
    evidence_id TEXT UNIQUE,
    authorized_at TEXT NOT NULL,
    started_at TEXT,
    imported_at TEXT,
    abandoned_at TEXT,
    diagnostic_code TEXT,
    PRIMARY KEY (attempt_id, round_no),
    FOREIGN KEY (attempt_id) REFERENCES flaky_verification_attempt(attempt_id),
    CHECK (
        (status = 'AUTHORIZED' AND started_at IS NULL AND imported_at IS NULL
         AND abandoned_at IS NULL AND evidence_id IS NULL)
        OR
        (status = 'STARTED' AND started_at IS NOT NULL AND imported_at IS NULL
         AND abandoned_at IS NULL AND evidence_id IS NULL)
        OR
        (status = 'IMPORTED' AND started_at IS NOT NULL AND imported_at IS NOT NULL
         AND abandoned_at IS NULL AND evidence_id IS NOT NULL)
        OR
        (status = 'ABANDONED' AND abandoned_at IS NOT NULL AND imported_at IS NULL
         AND evidence_id IS NULL)
    )
);

CREATE UNIQUE INDEX idx_flaky_probe_one_inflight_round
ON flaky_probe_round(attempt_id)
WHERE status IN ('AUTHORIZED', 'STARTED');

CREATE TABLE flaky_probe_capacity_slot (
    slot_id INTEGER PRIMARY KEY CHECK (slot_id = 1),
    trigger_id TEXT NOT NULL UNIQUE,
    acquired_at TEXT NOT NULL,
    FOREIGN KEY (trigger_id) REFERENCES flaky_probe_trigger(trigger_id)
);

CREATE TABLE flaky_probe_evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL,
    trigger_id TEXT NOT NULL,
    reported_trigger_id TEXT NOT NULL,
    round_no INTEGER NOT NULL CHECK (round_no >= 1),
    trusted_started_at TEXT NOT NULL,
    trusted_finished_at TEXT,
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
    envelope_schema_version TEXT,
    envelope_key_id TEXT,
    envelope_json TEXT CHECK (envelope_json IS NULL OR json_valid(envelope_json)),
    envelope_signature TEXT,
    envelope_verified INTEGER NOT NULL DEFAULT 0 CHECK (envelope_verified IN (0, 1)),
    p0_bundle_status TEXT,
    p0_manifest_sha256 TEXT,
    p0_file_hashes_json TEXT CHECK (
        p0_file_hashes_json IS NULL OR json_valid(p0_file_hashes_json)
    ),
    job_full_name TEXT,
    build_number INTEGER CHECK (build_number IS NULL OR build_number >= 1),
    actual_target_commit_sha TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES flaky_import_run(run_id),
    FOREIGN KEY (attempt_id) REFERENCES flaky_verification_attempt(attempt_id),
    FOREIGN KEY (trigger_id) REFERENCES flaky_probe_trigger(trigger_id)
    ,CHECK (
        envelope_verified = 0
        OR (
            envelope_schema_version IS NOT NULL
            AND envelope_key_id IS NOT NULL
            AND envelope_json IS NOT NULL
            AND envelope_signature IS NOT NULL
            AND job_full_name IS NOT NULL
            AND build_number IS NOT NULL
            AND actual_target_commit_sha IS NOT NULL
            AND trusted_finished_at IS NOT NULL
        )
    )
);

CREATE UNIQUE INDEX idx_flaky_probe_one_applied_round
ON flaky_probe_evidence(attempt_id, round_no)
WHERE effect_status = 'APPLIED';

CREATE INDEX idx_flaky_probe_attempt_order
ON flaky_probe_evidence(attempt_id, round_no, trusted_started_at, run_id);

INSERT INTO flaky_probe_plan (
    attempt_id, governance_id, flaky_key, plan_version, canonical_json,
    plan_digest, case_id, param_hash, environment, execution_profile,
    state_epoch, target_branch, target_commit_sha, controller_commit_sha,
    policy_revision, probe_evidence_rule_version, fact_schema_version,
    required_consecutive_passes, min_interval_minutes, max_attempt_age_hours,
    max_non_counting_runs, max_dispatch_attempts, max_orchestration_rounds,
    allowed_job_full_name, created_at
)
SELECT
    attempt.attempt_id,
    attempt.governance_id,
    governance.flaky_key,
    'legacy-flaky-probe-plan.v0',
    '{"legacy":true}',
    trigger.plan_digest,
    identity.case_id,
    identity.param_hash,
    identity.environment,
    identity.execution_profile,
    identity.state_epoch,
    'dev3',
    attempt.target_commit_sha,
    attempt.target_commit_sha,
    attempt.policy_revision,
    'flaky-probe-evidence.v1',
    'quality.v2',
    attempt.required_consecutive_passes,
    attempt.min_interval_minutes,
    72,
    attempt.max_non_counting_runs,
    1,
    10,
    'legacy-local-probe',
    attempt.created_at
FROM flaky_verification_attempt AS attempt
JOIN flaky_governance AS governance
  ON governance.governance_id = attempt.governance_id
JOIN flaky_identity AS identity
  ON identity.flaky_key = governance.flaky_key
JOIN flaky_probe_trigger_v3 AS trigger
  ON trigger.attempt_id = attempt.attempt_id;

INSERT INTO flaky_probe_trigger (
    trigger_id, attempt_id, request_id, payload_hash, plan_digest,
    target_commit_sha, status, allowed_job_full_name, terminal_at,
    created_at, updated_at
)
SELECT
    trigger_id,
    attempt_id,
    request_id,
    'sha256:0000000000000000000000000000000000000000000000000000000000000000',
    plan_digest,
    target_commit_sha,
    CASE status
        WHEN 'EVIDENCE_COMPLETE' THEN 'COMPLETED'
        WHEN 'CANCELLED' THEN 'CANCELLED'
        ELSE 'PENDING'
    END,
    'legacy-local-probe',
    CASE WHEN status IN ('EVIDENCE_COMPLETE', 'CANCELLED') THEN updated_at END,
    created_at,
    updated_at
FROM flaky_probe_trigger_v3;

INSERT INTO flaky_probe_evidence (
    evidence_id, run_id, attempt_id, trigger_id, reported_trigger_id,
    round_no, trusted_started_at, raw_outcome, p0_trusted,
    rerun_supported, trusted_failure, plan_matches, arrived_after_terminal,
    classification, primary_reason_code, diagnostic_codes_json,
    consumes_non_counting_quota, effect_status, admission_rule_version,
    created_at
)
SELECT
    evidence_id, run_id, attempt_id, trigger_id, reported_trigger_id,
    round_no, trusted_started_at, raw_outcome, p0_trusted,
    rerun_supported, trusted_failure, plan_matches, arrived_after_terminal,
    classification, primary_reason_code, diagnostic_codes_json,
    consumes_non_counting_quota, effect_status, admission_rule_version,
    created_at
FROM flaky_probe_evidence_v3;

INSERT INTO flaky_probe_round (
    attempt_id, round_no, status, run_id, evidence_id,
    authorized_at, started_at, imported_at
)
SELECT
    attempt_id, round_no, 'IMPORTED', run_id, evidence_id,
    trusted_started_at, trusted_started_at, created_at
FROM flaky_probe_evidence
WHERE effect_status = 'APPLIED';

DROP TABLE flaky_probe_evidence_v3;
DROP TABLE flaky_probe_trigger_v3;
