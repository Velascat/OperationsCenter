"""
openclaw_shell — optional outer-shell integration for OpenClaw.

OpenClaw is an optional operator/runtime layer that can wrap the existing
contract-owned architecture without replacing it. This package provides:

  - OperatorContext: shell-level input (operator intent → planning input)
  - OpenClawShellService: thin internal service boundary
  - OpenClawBridge: explicit crossing point between shell and internals
  - Shell status/inspection models derived from canonical internal data

The system remains fully operational without this package. Nothing in
contracts, routing, backends, or observability imports from here.

Entry points:
    from control_plane.openclaw_shell.bridge import OpenClawBridge
    from control_plane.openclaw_shell.service import OpenClawShellService
    from control_plane.openclaw_shell.models import OperatorContext
"""

OPENCLAW_SHELL_VERSION = "0.1.0-phase10"
