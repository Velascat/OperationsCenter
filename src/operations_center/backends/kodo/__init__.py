# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/kodo — canonical kodo backend adapter.

Public entry point:
    from operations_center.backends.kodo import KodoBackendAdapter
"""

from .adapter import KodoBackendAdapter
from .models import SupportCheck

__all__ = ["KodoBackendAdapter", "SupportCheck"]
