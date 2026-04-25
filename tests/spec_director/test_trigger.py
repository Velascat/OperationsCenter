# tests/spec_director/test_trigger.py
from __future__ import annotations
from pathlib import Path


def test_drop_file_trigger(tmp_path):
    from operations_center.spec_director.trigger import TriggerDetector
    from operations_center.spec_director.models import TriggerSource
    drop = tmp_path / "spec_direction.md"
    drop.write_text("add webhook ingestion")
    detector = TriggerDetector(drop_file_path=drop)
    result = detector.detect(ready_count=5, running_count=2, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.DROP_FILE
    assert result.seed_text == "add webhook ingestion"


def test_drop_file_not_triggered_when_campaign_active(tmp_path):
    from operations_center.spec_director.trigger import TriggerDetector
    drop = tmp_path / "spec_direction.md"
    drop.write_text("something")
    detector = TriggerDetector(drop_file_path=drop)
    result = detector.detect(ready_count=0, running_count=0, has_active_campaign=True)
    assert result is None


def test_queue_drain_trigger():
    from operations_center.spec_director.trigger import TriggerDetector
    from operations_center.spec_director.models import TriggerSource
    detector = TriggerDetector(drop_file_path=Path("/nonexistent"))
    result = detector.detect(ready_count=0, running_count=0, has_active_campaign=False)
    assert result is not None
    assert result.source == TriggerSource.QUEUE_DRAIN
    assert result.seed_text == ""


def test_no_trigger_when_queue_full():
    from operations_center.spec_director.trigger import TriggerDetector
    detector = TriggerDetector(drop_file_path=Path("/nonexistent"))
    result = detector.detect(ready_count=5, running_count=0, has_active_campaign=False)
    assert result is None
