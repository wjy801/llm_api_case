CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE flaky_import_run (
    run_id TEXT PRIMARY KEY,
    source_digest TEXT NOT NULL UNIQUE,
    source_kind TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,
    job_name TEXT,
    build_number TEXT,
    branch TEXT,
    commit_sha TEXT,
    environment TEXT NOT NULL,
    run_status TEXT NOT NULL,
    p0_integrity_status TEXT NOT NULL,
    run_start_time TEXT NOT NULL,
    run_end_time TEXT NOT NULL,
    p0_schema_version TEXT NOT NULL,
    p0_merge_version TEXT NOT NULL,
    fingerprint_version TEXT NOT NULL,
    run_record_sha256 TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    case_results_sha256 TEXT NOT NULL,
    failures_sha256 TEXT NOT NULL,
    integrity_issues_sha256 TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    identity_rule_version TEXT NOT NULL,
    environment_rule_version TEXT NOT NULL,
    execution_profile_rule_version TEXT NOT NULL,
    observation_rule_version TEXT NOT NULL,
    eligible_count INTEGER NOT NULL CHECK (eligible_count >= 0),
    excluded_count INTEGER NOT NULL CHECK (excluded_count >= 0),
    imported_at TEXT NOT NULL
);

CREATE TABLE flaky_case_epoch (
    epoch_scope_key TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    execution_profile TEXT NOT NULL,
    current_epoch INTEGER NOT NULL CHECK (current_epoch >= 1),
    identity_rule_version TEXT NOT NULL,
    environment_rule_version TEXT NOT NULL,
    execution_profile_rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (case_id, environment, execution_profile)
);

CREATE TABLE case_observation (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    invocation_id TEXT NOT NULL,
    flaky_key TEXT NOT NULL,
    epoch_scope_key TEXT NOT NULL,
    case_id TEXT NOT NULL,
    param_hash TEXT NOT NULL,
    environment TEXT NOT NULL,
    execution_profile TEXT NOT NULL,
    state_epoch INTEGER NOT NULL CHECK (state_epoch >= 1),
    decisive_phase TEXT NOT NULL,
    raw_status TEXT NOT NULL,
    final_status TEXT NOT NULL,
    observation_outcome TEXT NOT NULL CHECK (observation_outcome IN ('pass', 'fail')),
    failure_id TEXT,
    failure_category TEXT,
    observed_at TEXT NOT NULL,
    identity_rule_version TEXT NOT NULL,
    environment_rule_version TEXT NOT NULL,
    execution_profile_rule_version TEXT NOT NULL,
    observation_rule_version TEXT NOT NULL,
    fingerprint_version TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES flaky_import_run(run_id),
    FOREIGN KEY (epoch_scope_key) REFERENCES flaky_case_epoch(epoch_scope_key),
    UNIQUE (run_id, flaky_key),
    CHECK (
        (observation_outcome = 'pass' AND failure_id IS NULL)
        OR
        (observation_outcome = 'fail' AND failure_id IS NOT NULL)
    )
);

CREATE INDEX idx_case_observation_flaky_time
ON case_observation(flaky_key, observed_at, run_id);

CREATE INDEX idx_case_observation_case_lookup
ON case_observation(case_id, environment, execution_profile, state_epoch);

CREATE TABLE flaky_override (
    override_id TEXT PRIMARY KEY,
    epoch_scope_key TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action = 'reset_epoch'),
    previous_epoch INTEGER NOT NULL CHECK (previous_epoch >= 1),
    new_epoch INTEGER NOT NULL CHECK (new_epoch = previous_epoch + 1),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (epoch_scope_key) REFERENCES flaky_case_epoch(epoch_scope_key)
);

CREATE INDEX idx_flaky_override_scope_time
ON flaky_override(epoch_scope_key, created_at);
