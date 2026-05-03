# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json

from operations_center.entrypoints.setup.providers import detect_all_provider_statuses


def main() -> None:
    statuses = detect_all_provider_statuses()
    print(
        json.dumps(
            [
                {
                    "key": status.key,
                    "label": status.label,
                    "installed": status.installed,
                    "version": status.version,
                    "auth_mode": status.auth_mode,
                    "interactive_ready": status.interactive_ready,
                    "headless_ready": status.headless_ready,
                    "detail": status.detail,
                }
                for status in statuses
            ],
            indent=2,
        ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
