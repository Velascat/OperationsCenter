"""
backends/kodo — canonical kodo backend adapter.

Public entry point:
    from operations_center.backends.kodo import KodoBackendAdapter
"""

from .adapter import KodoBackendAdapter
from .models import SupportCheck

__all__ = ["KodoBackendAdapter", "SupportCheck"]
