from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

from quality.flaky_store import MIGRATIONS_DIRECTORY
from quality.flaky_store.migration import load_migrations


EXPECTED_RAW_HASHES = {
    "0001_observation_store.sql": "4a0c5601080b1b25936f5879c4e8076c8c0b3227f72b08fcaf1c45e9b3153276",
    "0002_flaky_state_machine.sql": "730a10c7d6ab00c6535106285b4d4d2498c7d5b9a8aa46fde403484a986b6fa1",
    "0003_v3_state_machine.sql": "0d2afe0c0545427da8bb81cbdb4aa515507c4a02f0a91adcc49dce0b3ec6d857",
}
EXPECTED_MIGRATION_CHECKSUMS = {
    1: "6193bd75d139e8cfddaac171db85facdf6b102416f65401a490a18c92699cbe6",
    2: "4892ff20bb9d8fdca6b784314fbb9487fafa85de74a5dd44e5bd213ee8c74b69",
    3: "0d2afe0c0545427da8bb81cbdb4aa515507c4a02f0a91adcc49dce0b3ec6d857",
}


def test_flaky_migrations_keep_names_raw_bytes_and_normalized_checksums():
    actual_files = {path.name for path in MIGRATIONS_DIRECTORY.glob("*.sql")}
    assert actual_files == set(EXPECTED_RAW_HASHES)

    for name, expected in EXPECTED_RAW_HASHES.items():
        assert hashlib.sha256((MIGRATIONS_DIRECTORY / name).read_bytes()).hexdigest() == expected

    migrations = load_migrations(MIGRATIONS_DIRECTORY)
    assert tuple(item.version for item in migrations) == (1, 2, 3)
    assert {item.version: item.checksum for item in migrations} == EXPECTED_MIGRATION_CHECKSUMS


def test_old_flaky_migration_directory_has_no_duplicate_assets():
    old_directory = MIGRATIONS_DIRECTORY.parents[1] / "migrations" / "flaky"
    assert not old_directory.exists()


def test_custom_migration_directory_keeps_the_same_discovery_contract(tmp_path):
    custom = tmp_path / "migrations"
    custom.mkdir()
    for name in EXPECTED_RAW_HASHES:
        shutil.copy2(MIGRATIONS_DIRECTORY / name, custom / name)

    migrations = load_migrations(custom)

    assert tuple(item.name for item in migrations) == tuple(EXPECTED_RAW_HASHES)
    assert {item.version: item.checksum for item in migrations} == EXPECTED_MIGRATION_CHECKSUMS
