"""Reviewer cutover entrypoint.

The legacy reviewer loop previously coupled ControlPlane to execution and PR
mutation flows. After the architecture remediation, ControlPlane no longer owns
execution. This entrypoint remains only to explain the cutover and point
operators at retained proposal/decision artifacts.
"""

from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explain the ControlPlane reviewer cutover."
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    payload = {
        "status": "retired",
        "message": (
            "ControlPlane no longer runs the legacy reviewer execution loop. "
            "It stops at TaskProposal and LaneDecision handoff."
        ),
    }
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(payload["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
