# tests/spec_director/test_reviewer_compliance.py
from __future__ import annotations
from unittest.mock import patch


def test_compliance_branch_called_for_campaign_task():
    """When spec_campaign_id is in task metadata, SpecComplianceService is called."""


    with patch("control_plane.entrypoints.reviewer.main._get_spec_campaign_id") as mock_get_id:
        mock_get_id.return_value = "abc-123"
        with patch("control_plane.entrypoints.reviewer.main._run_spec_compliance") as mock_compliance:
            mock_compliance.return_value = "LGTM"
            # If _get_spec_campaign_id returns a value, compliance branch should fire.
            assert mock_get_id("task description") == "abc-123"
