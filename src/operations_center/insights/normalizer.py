# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime

from operations_center.insights.models import DerivedInsight


class InsightNormalizer:
    def normalize(
        self,
        *,
        kind: str,
        subject: str,
        status: str,
        key_parts: list[str],
        evidence: dict[str, object],
        first_seen_at: datetime,
        last_seen_at: datetime,
    ) -> DerivedInsight:
        dedup_key = "|".join([kind, *key_parts])
        insight_id = dedup_key.replace("|", ":")
        return DerivedInsight(
            insight_id=insight_id,
            dedup_key=dedup_key,
            kind=kind,
            subject=subject,
            status=status,
            evidence=evidence,
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
        )
