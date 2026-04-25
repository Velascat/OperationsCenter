from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from operations_center.tuning.applier import TuningApplier
from operations_center.tuning.artifact_writer import TuningArtifactWriter
from operations_center.tuning.guardrails import AUTO_APPLY_KEYS, TuningGuardrails
from operations_center.tuning.loader import TuningArtifactLoader
from operations_center.tuning.metrics import aggregate_family_metrics
from operations_center.tuning.models import TuningChange, TuningRunArtifact
from operations_center.tuning.recommendations import RecommendationEngine


@dataclass(frozen=True)
class TuningContext:
    run_id: str
    generated_at: datetime
    source_command: str
    decision_root: Path
    proposer_root: Path
    auto_apply: bool = False
    window: int = 20
    dry_run: bool = False  # if True, skip artifact write (useful for testing)


def new_tuning_context(
    *,
    decision_root: Path,
    proposer_root: Path,
    auto_apply: bool = False,
    window: int = 20,
    source_command: str = "operations-center tune-autonomy",
) -> TuningContext:
    now = datetime.now(UTC)
    run_id = f"tun_{now.strftime('%Y%m%dT%H%M%SZ')}_{now.microsecond:06x}"[-31:]
    return TuningContext(
        run_id=run_id,
        generated_at=now,
        source_command=source_command,
        decision_root=decision_root,
        proposer_root=proposer_root,
        auto_apply=auto_apply,
        window=window,
    )


class TuningRegulatorService:
    """Bounded self-tuning regulation loop.

    Supported runtime posture: recommendation-only. The regulator may retain
    what would have been auto-apply candidates as skipped changes for review,
    but it never mutates live tuning config automatically.
    """

    def __init__(
        self,
        *,
        recommendation_engine: RecommendationEngine | None = None,
        guardrails: TuningGuardrails | None = None,
        applier: TuningApplier | None = None,
        loader: TuningArtifactLoader | None = None,
        artifact_writer: TuningArtifactWriter | None = None,
    ) -> None:
        self.engine = recommendation_engine or RecommendationEngine()
        self.guardrails = guardrails or TuningGuardrails()
        self.applier = applier or TuningApplier()
        self.loader = loader or TuningArtifactLoader()
        self.writer = artifact_writer or TuningArtifactWriter()

    def run(self, context: TuningContext) -> tuple[TuningRunArtifact, list[str]]:
        # 1. Aggregate family metrics from retained artifacts
        family_metrics, sample_runs, window_start, window_end = aggregate_family_metrics(
            decision_root=context.decision_root,
            proposer_root=context.proposer_root,
            window=context.window,
        )

        # 2. Generate recommendations
        recommendations = self.engine.evaluate(family_metrics)

        # 3. Runtime truth posture: tuning remains recommendation-only.
        changes_applied: list[TuningChange] = []
        from operations_center.tuning.models import SkippedTuningChange
        changes_skipped: list[SkippedTuningChange] = []

        if context.auto_apply:
            for rec in recommendations:
                if rec.action in ("loosen_threshold", "tighten_threshold"):
                    changes_skipped.append(
                        SkippedTuningChange(
                            family=rec.family,
                            intended_action=rec.action,
                            reason="review_only_runtime",
                            evidence={
                                "requested_auto_apply": True,
                                "sample_runs": sample_runs,
                            },
                        )
                    )

        artifact = TuningRunArtifact(
            run_id=context.run_id,
            generated_at=context.generated_at,
            source_command=context.source_command,
            dry_run=True,
            auto_apply=False,
            window_runs=sample_runs,
            window_start=window_start,
            window_end=window_end,
            family_metrics=family_metrics,
            recommendations=recommendations,
            changes_applied=changes_applied,
            changes_skipped=changes_skipped,
        )

        if context.dry_run:
            return artifact, []

        paths = self.writer.write(artifact)
        return artifact, paths
