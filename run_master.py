from __future__ import annotations

import os
import sys

from run_orchestration import (
    DEFAULT_ALLURE_RESULTS_DIR,
    PROJECT_ROOT,
    main,
    run,
)


__all__ = (
    "DEFAULT_ALLURE_RESULTS_DIR",
    "PROJECT_ROOT",
    "main",
    "run",
)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main(sys.argv[1:]))
