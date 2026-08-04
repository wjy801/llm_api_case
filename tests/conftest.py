from __future__ import annotations

import os


# Framework tests must be collectable in a clean checkout without a developer
# .env file. These values are process-local, synthetic, and never used for real
# interface execution.
os.environ.update(
    {
        "USE_CHINA_ENVIRONMENT": "FALSE",
        "OVERSEAS_TEST_BASE_URL": "https://offline.invalid",
        "OVERSEAS_API_KEY": "offline-test-key",
        "OVERSEAS_CONTROL_API_KEY": "offline-control-key",
        "RUN_REAL_ENV_TESTS": "FALSE",
        "GENERATE_ALLURE_REPORT": "FALSE",
        "GENERATE_HISTORY_REPORT": "FALSE",
    }
)
