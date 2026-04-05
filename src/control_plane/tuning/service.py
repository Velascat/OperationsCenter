from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from control_plane.tuning.applier import TuningApplier
from control_plane.tuning.artifact_writer import TuningArtifactWriter
from control_plane.tuning.guardrails import AUTO_APPLY_KEYS, TuningGuardrails
from control_plane.tuning.loader import TuningArtifactLoader
from control_plane.tuning.metrics import aggregate_family_metrics
from control_plane.tuning.models import TuningChange, TuningRunArtifact
from control_plane.tuning.recommendations import RecommendationEngine


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
    source_command: str = "control-plane tune-autonomy",
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

    Default mode: recommendation-only (no config mutation).
    Auto-apply mode (opt-in): applies small bounded changes within guardrails.

    Every run produces retained artifacts regardless of mode.
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

        # 3. If auto-apply, evaluate each actionable recommendation through guardrails
        changes_applied: list[TuningChange] = []
        from control_plane.tuning.models import SkippedTuningChange
        changes_skipped: list[SkippedTuningChange] = []

        if context.auto_apply:
            prior_runs = self.loader.load_recent(limit=30)

            for rec in recommendations:
                for key in AUTO_APPLY_KEYS:
                    # Only process families/keys that have an apply path
                    if rec.action not in ("loosen_threshold", "tighten_threshold"):
                        continue
                    current_val = self.applier.current_value(rec.family, key)
                    can_apply, skip_reason = self.guardrails.evaluate(
                        recommendation=rec,
                        current_value=current_val,
                        prior_runs=prior_runs,
                        changes_so_far=changes_applied,
                        generated_at=context.generated_at,
                        sample_runs=sample_runs,
                    )
                    if can_apply:
                        change = self.applier.apply(
                            family=rec.family,
                            key=key,
                            action=rec.action,
                            reason=rec.rationale,
                            generated_at=context.generated_at,
                        )
                        if change is not None:
                            changes_applied.append(change)
                    else:
                        changes_skipped.append(
                            self.guardrails.build_skipped(rec, skip_reason, sample_runs)
                        )
                        break  # only record one skip per recommendation

        artifact = TuningRunArtifact(
            run_id=context.run_id,
            generated_at=context.generated_at,
            source_command=context.source_command,
            dry_run=not context.auto_apply,
            auto_apply=context.auto_apply,
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
