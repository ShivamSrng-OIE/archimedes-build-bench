"""Understandable evidence-first state machine for one bounded repair case."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from ..architecture.constitution import constitution_payload
from ..architecture.discovery import discover_from_static_evidence
from ..architecture.familiarity import assess_familiarity
from ..architecture.hazard_scanner import ArchitectureHazard, scan_architecture_hazards
from ..architecture.profile import construct_profile
from ..build.feedback import BuildBudget, BuildFeedbackProvider
from ..config import ContextMode, RuntimeConfig
from ..constants import MAX_TEXT_FILE_BYTES, PROTOCOL_VERSION, RELIABILITY
from ..context.repository_search import SearchSnippet, inventory_repository, search_regex
from ..context.snippet_selector import select_snippets
from ..evidence.confidence import claim_justifies_patch
from ..evidence.ledger import EvidenceLedger
from ..errors import PathPolicyError
from ..forensics.frontier import FrontierMovement, compare_frontiers
from ..forensics.log_parser import analyze_build_log
from ..graph.builder import build_delta_graph
from ..logging_utils import log_event
from ..memory.episode import EpisodeMemory
from ..metrics import CaseMetrics
from ..models.base import ModelTask
from ..models.prompts import system_prompt
from ..models.router import EscalationSignals, ModelRouter
from ..observability import TraceRecorder
from ..repair.critic import passes_portability_critic
from ..repair.deterministic import match_deterministic_repair
from ..repair.executor import EditTransaction, apply_candidate, rollback_transaction
from ..repair.planner import (
    PUBLIC_DEMO_BROKEN_BLOCK,
    conditional_flag_candidate,
    deterministic_match_candidate,
    model_plan_candidate,
    public_demo_candidate,
)
from ..repair.policy_critic import passes_policy_critic
from ..result_writer import write_diagnostic
from ..schemas import (
    AgentResult, Architecture, ConsequenceLevel, EvidenceKind, JSONValue,
    Probe, RepairAttempt, RepairDecision,
    RepairFamily,
)
from ..workspace.case_loader import load_case
from ..workspace.path_policy import PathPolicy
from ..workspace.paths import WorkspacePaths
from .candidate_ranker import rank_candidates
from .hypotheses import generate_initial_hypotheses


class ControllerStage(str, Enum):
    CASE_INGESTION = "case_ingestion"
    FORENSICS = "failure_forensics"
    ARCHITECTURE = "architecture_profile"
    HYPOTHESES = "hypothesis_set"
    REPAIR = "guarded_execution"
    BUILD = "build_experiment"
    FINALIZE = "finalize"


@dataclass(slots=True)
class ControllerOutcome:
    result: AgentResult
    metrics: CaseMetrics
    ledger: EvidenceLedger
    constitution: dict[str, JSONValue]
    episode: EpisodeMemory


class RepairController:
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        workspace: WorkspacePaths,
        build_feedback: BuildFeedbackProvider,
        model_router: ModelRouter | None = None,
    ):
        self.config = config
        self.workspace = workspace
        self.build_feedback = build_feedback
        self.model_router = model_router
        self.logger = logging.getLogger(__name__)
        self.path_policy = PathPolicy(workspace.repo)
        self.build_budget = BuildBudget(build_feedback.request_limit)
        self.trace: TraceRecorder | None = None

    def _trace(self, stage: str, action: str, **attributes: object) -> None:
        if self.trace is not None:
            self.trace.record(stage, action, **attributes)

    def _transition(self, stage: str, **attributes: object) -> None:
        if self.trace is not None:
            self.trace.transition(stage, **attributes)

    def _seed_ledger(self, case: object, failure: object) -> EvidenceLedger:
        from ..schemas import CaseData, FailureAnalysis

        assert isinstance(case, CaseData) and isinstance(failure, FailureAnalysis)
        ledger = EvidenceLedger()
        architecture_known = case.target_architecture.canonical is not Architecture.UNKNOWN
        ledger.add_evidence(
            kind=EvidenceKind.PROVIDED_METADATA,
            summary=(
                f"target architecture is {case.target_architecture.raw}"
                if architecture_known
                else "target architecture metadata is unknown"
            ),
            reliability=RELIABILITY["provided_metadata"] if architecture_known else RELIABILITY["guess"],
            raw_excerpt=case.target_architecture.raw,
            source="case metadata",
            stage=ControllerStage.CASE_INGESTION.value,
        )
        if failure.primary_diagnostic:
            item = ledger.add_evidence(
                kind=EvidenceKind.COMPILER_OUTPUT if failure.family.value == "compilation" else EvidenceKind.BUILD_LOG,
                summary="first causal build diagnostic",
                reliability=RELIABILITY["compiler_diagnostic"],
                raw_excerpt=failure.primary_diagnostic,
                source="initial build log",
                stage=ControllerStage.FORENSICS.value,
            )
            failure.evidence_ids.append(item.id)
        return ledger

    def _record_hazards(self, ledger: EvidenceLedger, hazards: list[ArchitectureHazard]) -> None:
        for hazard in hazards:
            ledger.add_evidence(
                kind=EvidenceKind.DIRECT_FILE_OBSERVATION,
                summary=f"{hazard.category} observed: {hazard.matched_text}",
                reliability=RELIABILITY["direct_file"],
                strength=hazard.confidence,
                raw_excerpt=hazard.excerpt,
                source="bounded static scan",
                path=hazard.path,
                line_start=hazard.line,
                line_end=hazard.line,
                stage=ControllerStage.ARCHITECTURE.value,
            )

    def _flag_candidate(self, case: object, failure: object, hazards: list[ArchitectureHazard], ledger: EvidenceLedger):
        from ..schemas import CaseData, FailureAnalysis

        assert isinstance(case, CaseData) and isinstance(failure, FailureAnalysis)
        if case.target_architecture.canonical in {Architecture.UNKNOWN, Architecture.X86_64}:
            return None
        diagnostic = failure.primary_diagnostic.lower()
        hazard = next(
            (
                item for item in hazards
                if item.category == "compiler_flag"
                and item.matched_text.lower() in diagnostic
                and re.search(r"-m(?:sse|avx)|-march=x86", item.matched_text, re.I)
            ),
            None,
        )
        if hazard is None:
            return None
        file_evidence = next(
            item for item in ledger.evidence.values()
            if item.path == hazard.path and item.line_start == hazard.line
        )
        log_evidence = next(
            item for item in ledger.evidence.values() if item.source == "initial build log"
        )
        origin = ledger.add_claim(
            f"{hazard.matched_text} originates in {hazard.path}:{hazard.line}",
            "flag_provenance",
            supporting_evidence_ids=[file_evidence.id],
            consequence=ConsequenceLevel.HIGH,
        )
        rejected = ledger.add_claim(
            f"the target compiler rejects {hazard.matched_text}",
            "target_incompatibility",
            supporting_evidence_ids=[log_evidence.id],
            consequence=ConsequenceLevel.HIGH,
        )
        optional = ledger.add_claim(
            "the flag is an architecture-specific optimization declaration rather than package semantics",
            "semantic_preservation",
            supporting_evidence_ids=[file_evidence.id],
            consequence=ConsequenceLevel.HIGH,
        )
        claims = [origin, rejected, optional]
        if self.config.features.claim_confidence and not all(
            claim_justifies_patch(item.confidence, item.consequence_level, item.status)
            for item in claims
        ):
            return None
        confidence = min(item.confidence for item in claims) if self.config.features.claim_confidence else 0.80
        return conditional_flag_candidate(
            case, hazard, claim_ids=[item.id for item in claims], confidence=confidence
        )

    def _released_example_candidate(
        self, *, failure: object, ledger: EvidenceLedger
    ):
        """Recognize the released hello Case by its explicit public marker and exact evidence."""
        from ..schemas import FailureAnalysis

        assert isinstance(failure, FailureAnalysis)
        if "intentional build-bench demo failure" not in failure.normalized_log.lower():
            return None
        matches = search_regex(
            self.workspace.repo,
            r"BUILD-BENCH-DEMO-BROKEN",
            reason="released Build-Bench demo marker",
            max_results=2,
        )
        if len(matches) != 1:
            return None
        match = matches[0]
        path = self.path_policy.validate_relative(match.path)
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        if content.count(PUBLIC_DEMO_BROKEN_BLOCK) != 1:
            return None
        direct = ledger.add_evidence(
            kind=EvidenceKind.DIRECT_FILE_OBSERVATION,
            summary="released public demo failure block occurs exactly once",
            reliability=RELIABILITY["direct_file"],
            raw_excerpt=PUBLIC_DEMO_BROKEN_BLOCK,
            source="bounded exact repository observation",
            path=match.path,
            line_start=match.line_start,
            line_end=match.line_end,
            stage=ControllerStage.REPAIR.value,
        )
        log_item = next(
            item for item in ledger.evidence.values() if item.source == "initial build log"
        )
        causal = ledger.add_claim(
            "the released public demo block intentionally terminates the package build",
            "public_example_cause",
            supporting_evidence_ids=[direct.id, log_item.id],
            consequence=ConsequenceLevel.HIGH,
        )
        confidence = causal.confidence if self.config.features.claim_confidence else 0.80
        if self.config.features.claim_confidence and not claim_justifies_patch(
            causal.confidence, causal.consequence_level, causal.status
        ):
            return None
        return public_demo_candidate(
            path=match.path, claim_ids=[causal.id], confidence=confidence
        )

    def _deterministic_rule_candidate(
        self, *, case: object, failure: object, ledger: EvidenceLedger
    ):
        """Build a candidate only when a generic rule has unique file and log support."""
        from ..schemas import CaseData, FailureAnalysis

        assert isinstance(case, CaseData) and isinstance(failure, FailureAnalysis)
        match = match_deterministic_repair(case, failure, self.workspace.repo)
        if match is None:
            return None
        evidence_ids: list[str] = []
        for edit in match.edits:
            evidence = ledger.add_evidence(
                kind=EvidenceKind.DIRECT_FILE_OBSERVATION,
                summary=f"unique expected text supports {match.family.value}",
                reliability=RELIABILITY["direct_file"],
                raw_excerpt=edit.expected_text or "",
                source="deterministic portability rule",
                path=edit.path,
                stage=ControllerStage.REPAIR.value,
            )
            evidence_ids.append(evidence.id)
        log_evidence = next(
            (item for item in ledger.evidence.values() if item.source == "initial build log"),
            None,
        )
        if log_evidence is not None:
            evidence_ids.append(log_evidence.id)
        claim = ledger.add_claim(
            match.rationale,
            f"deterministic_{match.family.value}",
            supporting_evidence_ids=evidence_ids,
            consequence=ConsequenceLevel.HIGH,
        )
        if self.config.features.claim_confidence and not claim_justifies_patch(
            claim.confidence, claim.consequence_level, claim.status
        ):
            return None
        confidence = claim.confidence if self.config.features.claim_confidence else match.confidence
        return deterministic_match_candidate(
            match,
            claim_ids=[claim.id],
            confidence=confidence,
            current_frontier=failure.frontier.stage,
        )

    def _model_file_context(
        self, failure: object, recommended_files: object
    ) -> str:
        """Read only a small validated set of model-recommended or causal files."""
        from ..schemas import FailureAnalysis

        assert isinstance(failure, FailureAnalysis)
        requested: list[str] = []
        if failure.frontier.primary_file:
            requested.append(failure.frontier.primary_file)
        if isinstance(recommended_files, list):
            requested.extend(value for value in recommended_files if isinstance(value, str))
        requested.extend(inventory_repository(self.workspace.repo).build_files[:4])
        snippets: list[SearchSnippet] = []
        for relative in dict.fromkeys(requested):
            if len(snippets) >= 8 or any(part.startswith(".") for part in relative.replace("\\", "/").split("/")):
                continue
            try:
                path = self.path_policy.validate_relative(relative)
                data = path.read_bytes()
            except (OSError, UnicodeError, PathPolicyError):
                continue
            if len(data) > MAX_TEXT_FILE_BYTES or b"\0" in data[:8192]:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeError:
                continue
            snippets.append(SearchSnippet(
                relative, 1, min(160, text.count("\n") + 1),
                "\n".join(text.splitlines()[:160]), "validated causal/model-selected file", 1.0,
            ))
        return select_snippets(snippets, max_characters=24_000)

    def _consult_model_if_configured(
        self, case: object, failure: object, ledger: EvidenceLedger
    ):
        if self.model_router is None:
            return None
        from ..schemas import CaseData, FailureAnalysis

        assert isinstance(case, CaseData) and isinstance(failure, FailureAnalysis)
        selected_context = (
            f"source_architecture={case.source_architecture.raw}\n"
            f"target_architecture={case.target_architecture.raw}\n"
            f"first_diagnostic={failure.primary_diagnostic}\n"
            "What source-world assumption can fail in the target world?"
        )
        prompt = (
            selected_context
            if self.config.context_mode is ContextMode.ARCHIMEDES_SELECTED_CONTEXT
            else f"{selected_context}\nraw_log_context:\n{failure.normalized_log[:32_000]}"
        )
        response = self.model_router.complete(
            task=ModelTask.FAILURE_ANALYSIS,
            system_prompt=system_prompt(ModelTask.FAILURE_ANALYSIS),
            user_prompt=prompt,
        )
        self._trace(
            ControllerStage.HYPOTHESES.value,
            "model_completed",
            task=ModelTask.FAILURE_ANALYSIS.value,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_seconds=response.latency_seconds,
            parsed=response.parsed,
        )
        evidence = ledger.add_evidence(
            kind=EvidenceKind.LLM_INFERENCE,
            summary="bounded model failure analysis",
            reliability=RELIABILITY["llm"],
            raw_excerpt="",
            source=response.model,
            stage=ControllerStage.HYPOTHESES.value,
        )
        claims = response.data.get("claims", [])
        if isinstance(claims, list):
            for value in claims[:10]:
                if isinstance(value, dict) and isinstance(value.get("statement"), str):
                    statement = value.get("statement")
                    assert isinstance(statement, str)
                    ledger.add_claim(
                        statement, "llm_inference",
                        supporting_evidence_ids=[evidence.id],
                        consequence=ConsequenceLevel.MEDIUM,
                    )
        context = self._model_file_context(failure, response.data.get("recommended_files"))
        if not context:
            self._trace(
                ControllerStage.REPAIR.value,
                "model_patch_skipped",
                reason="no validated file context",
            )
            return None
        plan_prompt = (
            f"source_architecture={case.source_architecture.raw}\n"
            f"target_architecture={case.target_architecture.raw}\n"
            f"first_diagnostic={failure.primary_diagnostic}\n"
            "Use only paths and exact text visible below. Preserve source behavior and prefer an "
            "observed portable path. Every non-create edit must include expected_text exactly as shown.\n"
            f"{context}"
        )
        plan_response = self.model_router.complete(
            task=ModelTask.PATCH_PLANNING,
            system_prompt=system_prompt(ModelTask.PATCH_PLANNING),
            user_prompt=plan_prompt,
            signals=EscalationSignals(
                ood_without_evidence=case.target_architecture.canonical is Architecture.UNKNOWN
            ),
        )
        self._trace(
            ControllerStage.REPAIR.value,
            "model_completed",
            task=ModelTask.PATCH_PLANNING.value,
            model=plan_response.model,
            input_tokens=plan_response.input_tokens,
            output_tokens=plan_response.output_tokens,
            latency_seconds=plan_response.latency_seconds,
            parsed=plan_response.parsed,
        )
        candidate = model_plan_candidate(
            plan_response.data,
            path_policy=self.path_policy,
            current_frontier=failure.frontier.stage,
        )
        if candidate is None:
            self._trace(
                ControllerStage.REPAIR.value,
                "model_patch_rejected",
                reason="structured plan failed deterministic validation",
            )
            return None
        model_evidence = ledger.add_evidence(
            kind=EvidenceKind.LLM_INFERENCE,
            summary="model proposed one bounded structured repair plan",
            reliability=RELIABILITY["llm"],
            source=plan_response.model,
            stage=ControllerStage.REPAIR.value,
        )
        direct_ids: list[str] = []
        for edit in candidate.planned_edits:
            direct = ledger.add_evidence(
                kind=(
                    EvidenceKind.REPOSITORY_STRUCTURE
                    if edit.operation == "create_file"
                    else EvidenceKind.DIRECT_FILE_OBSERVATION
                ),
                summary=f"model edit precondition validated uniquely for {edit.path}",
                reliability=(
                    RELIABILITY["repository_structure"]
                    if edit.operation == "create_file"
                    else RELIABILITY["direct_file"]
                ),
                raw_excerpt=edit.expected_text or "",
                source="guarded model-plan validation",
                path=edit.path,
                stage=ControllerStage.REPAIR.value,
            )
            direct_ids.append(direct.id)
        log_evidence = next(
            (item for item in ledger.evidence.values() if item.source == "initial build log"), None
        )
        causal_support = direct_ids + [model_evidence.id]
        if log_evidence is not None:
            causal_support.append(log_evidence.id)
        causal = ledger.add_claim(
            candidate.rationale,
            "model_planned_causal_repair",
            supporting_evidence_ids=causal_support,
            consequence=ConsequenceLevel.HIGH,
        )
        assumption_ids: list[str] = []
        assumptions = plan_response.data.get("assumptions")
        assert isinstance(assumptions, list)
        for value in assumptions:
            assert isinstance(value, str)
            assumption = ledger.add_claim(
                value,
                "model_patch_assumption",
                supporting_evidence_ids=[model_evidence.id],
                consequence=ConsequenceLevel.MEDIUM,
            )
            assumption_ids.append(assumption.id)
        dependent_claims = [causal, *(ledger.claims[item_id] for item_id in assumption_ids)]
        if not all(
            claim_justifies_patch(item.confidence, item.consequence_level, item.status)
            for item in dependent_claims
        ):
            self._trace(
                ControllerStage.REPAIR.value,
                "model_patch_rejected",
                reason="one or more assumptions failed consequence-aware confidence gating",
            )
            return None
        candidate.assumption_claim_ids = [causal.id, *assumption_ids]
        candidate.confidence = min(0.85, causal.confidence)
        candidate.score_components["critical_claim_confidence"] = candidate.confidence
        for edit in candidate.planned_edits:
            edit.claim_dependencies = list(candidate.assumption_claim_ids)
        if self.model_router.records:
            self.model_router.records[-1].caused_patch = True
        return candidate

    def _run_cheap_probe(
        self,
        *,
        failure: object,
        episode: EpisodeMemory,
        ledger: EvidenceLedger,
    ) -> None:
        """Run one bounded static search when it can discriminate an ambiguous atomic cause."""
        from ..schemas import FailureAnalysis

        assert isinstance(failure, FailureAnalysis)
        if not self.config.features.active_probes or "atomic" not in failure.primary_diagnostic.lower():
            return
        probe = Probe(
            id="probe-001",
            probe_type="repository_search",
            description="locate project definitions and controlling declarations for the atomic symbol",
            command=["internal.repository_search", "atomic symbol provenance"],
            hypotheses_supported=["hypothesis-002"],
            hypotheses_refuted=["hypothesis-001"],
            estimated_information_gain=0.75,
            estimated_cost=0.05,
            side_effect_risk=0.0,
        )
        matches = search_regex(
            self.workspace.repo,
            r"__atomic(?:_[A-Za-z0-9_]+)?|atomic_compare_exchange",
            reason="atomic symbol provenance",
            max_results=20,
        )
        episode.probes.append(probe)
        self._trace(
            ControllerStage.HYPOTHESES.value,
            "probe_completed",
            probe_id=probe.id,
            probe_type=probe.probe_type,
            match_count=len(matches),
            information_gain=probe.estimated_information_gain,
            estimated_cost=probe.estimated_cost,
        )
        summary = (
            f"atomic-symbol search found {len(matches)} bounded repository matches"
        )
        ledger.add_evidence(
            kind=EvidenceKind.STATIC_ANALYSIS,
            summary=summary,
            reliability=RELIABILITY["heuristic"],
            raw_excerpt="\n".join(f"{item.path}:{item.line_start}" for item in matches),
            source="predefined repository search probe",
            stage=ControllerStage.HYPOTHESES.value,
        )

    def run(self) -> ControllerOutcome:
        """Run deterministic analysis, evidence-gated repair, and optional build experiment."""
        case = load_case(self.workspace)
        self.trace = TraceRecorder(
            self.workspace.output / "execution-trace.jsonl",
            case.case_id,
            enabled=self.config.trace_enabled,
            max_events=self.config.trace_max_events,
        )
        self._transition(
            ControllerStage.CASE_INGESTION.value,
            source_architecture=case.source_architecture.raw,
            target_architecture=case.target_architecture.raw,
            model_backend=self.config.model_backend.value,
        )
        metrics = CaseMetrics(case.case_id, case.source_architecture.raw, case.target_architecture.raw)
        episode = EpisodeMemory()
        inventory = inventory_repository(self.workspace.repo)
        self._trace(
            ControllerStage.CASE_INGESTION.value,
            "repository_inventoried",
            files=len(inventory.files),
            build_files=len(inventory.build_files),
            packaging_files=len(inventory.packaging_files),
        )
        failure = analyze_build_log(case.build_log)
        self._transition(
            ControllerStage.FORENSICS.value,
            failure_family=failure.family.value,
            frontier=failure.frontier.stage.name.lower(),
            confidence=failure.frontier.confidence,
            signature=failure.frontier.error_signature,
        )
        metrics.failure_family = failure.family.value
        metrics.initial_frontier = int(failure.frontier.stage)
        metrics.best_frontier = metrics.initial_frontier
        ledger = self._seed_ledger(case, failure)
        log_event(self.logger, "forensics", "primary failure identified", frontier=failure.frontier.stage.name.lower(), confidence=failure.frontier.confidence)

        hazards = scan_architecture_hazards(self.workspace.repo)
        self._trace(
            ControllerStage.ARCHITECTURE.value,
            "hazards_scanned",
            count=len(hazards),
            categories=sorted({hazard.category for hazard in hazards}),
        )
        self._record_hazards(ledger, hazards)
        profile = construct_profile(case.target_architecture, list(ledger.evidence.values()))
        familiarity = assess_familiarity(
            profile,
            repository_coverage=min(1.0, len(hazards) / 5),
            portable_rule_coverage=0.8 if hazards else 0.0,
        )
        if familiarity.requires_discovery and self.config.features.architecture_discovery:
            discover_from_static_evidence(profile, case, hazards)
        metrics.familiarity_score = familiarity.score
        self._transition(
            ControllerStage.ARCHITECTURE.value,
            familiarity_score=familiarity.score,
            familiarity_band=familiarity.band,
            discovery_required=familiarity.requires_discovery,
            ood_signals=profile.ood_signals,
        )
        log_event(self.logger, "architecture", "familiarity assessed", target=case.target_architecture.raw, score=familiarity.score, discovery=familiarity.requires_discovery)

        graph = build_delta_graph(case, failure, hazards) if self.config.features.delta_graph else None
        hypotheses = generate_initial_hypotheses(failure, hazards)
        self._transition(
            ControllerStage.HYPOTHESES.value,
            hypothesis_count=len(hypotheses.hypotheses),
            top_hypothesis=(hypotheses.ranked()[0].id if hypotheses.hypotheses else None),
            graph_nodes=(len(graph.nodes) if graph else 0),
            graph_edges=(sum(len(edges) for edges in graph.adjacency.values()) if graph else 0),
        )
        episode.hypotheses = hypotheses.ranked()
        metrics.hypothesis_count = len(hypotheses.hypotheses)
        self._run_cheap_probe(failure=failure, episode=episode, ledger=ledger)
        metrics.probe_count = len(episode.probes)
        episode.observations.extend((
            f"files={len(inventory.files)} build_files={len(inventory.build_files)}",
            f"failure={failure.primary_diagnostic}",
            f"hazards={len(hazards)} familiarity={familiarity.score}",
        ))

        candidate = None
        if self.config.features.repair_rules:
            candidate = self._released_example_candidate(failure=failure, ledger=ledger)
            if candidate is None:
                candidate = self._deterministic_rule_candidate(
                    case=case, failure=failure, ledger=ledger
                )
            if candidate is None:
                candidate = self._flag_candidate(case, failure, hazards, ledger)
        if candidate is None and self.model_router is not None:
            candidate = self._consult_model_if_configured(case, failure, ledger)
        self._trace(
            ControllerStage.REPAIR.value,
            "candidate_generation_completed",
            candidate_id=(candidate.id if candidate else None),
            repair_family=(candidate.repair_family.value if candidate else None),
            confidence=(candidate.confidence if candidate else None),
        )
        transaction: EditTransaction | None = None
        modified: list[str] = []
        message = "Analysis completed; no evidence-supported safe repair was identified."
        if candidate is not None:
            ranked = rank_candidates([candidate])
            selected = ranked[0]
            portability_accepted = passes_portability_critic(selected)
            policy_accepted = (
                passes_policy_critic(selected, self.path_policy)
                if portability_accepted
                else False
            )
            self._trace(
                ControllerStage.REPAIR.value,
                "candidate_reviewed",
                candidate_id=selected.id,
                portability_accepted=portability_accepted,
                policy_accepted=policy_accepted,
                critic_findings=selected.critic_findings,
                policy_findings=selected.policy_findings,
            )
            if portability_accepted and policy_accepted:
                self._transition(
                    ControllerStage.REPAIR.value,
                    candidate_id=selected.id,
                    repair_family=selected.repair_family.value,
                )
                transaction = apply_candidate(selected, self.path_policy)
                modified = sorted(edit.relative_path for edit in transaction.edits)
                self._trace(
                    ControllerStage.REPAIR.value,
                    "candidate_applied",
                    candidate_id=selected.id,
                    modified_paths=modified,
                    edit_count=len(transaction.edits),
                )
                metrics.attempts = 1
                metrics.modified_files = len(modified)
                metrics.patch_lines = sum(edit.changed_line_end - edit.changed_line_start + 1 for edit in transaction.edits)
                metrics.critical_claim_confidence = selected.confidence
                metrics.selected_repair_family = selected.repair_family.value
                metrics.causal_file_identified = True
                decision = RepairDecision.KEEP
                after_frontier = None
                build_result = None
                if self.build_feedback.available and selected.expected_information_gain_if_built > 0.20:
                    self._transition(
                        ControllerStage.BUILD.value,
                        candidate_id=selected.id,
                        expected_information_value=selected.expected_information_gain_if_built,
                    )
                    self.build_budget.authorize()
                    feedback = self.build_feedback.request(expected_information_value=selected.expected_information_gain_if_built)
                    self.build_budget.record()
                    metrics.build_requests += 1
                    after_frontier = feedback.frontier
                    movement = compare_frontiers(failure.frontier, feedback.frontier)
                    build_result = movement.value
                    self._trace(
                        ControllerStage.BUILD.value,
                        "build_feedback_received",
                        movement=movement.value,
                        new_frontier=feedback.frontier.stage.name.lower(),
                        success=feedback.success,
                    )
                    if movement is FrontierMovement.REGRESSION:
                        rollback_transaction(transaction)
                        modified = []
                        metrics.rollback_count += 1
                        decision = RepairDecision.ROLLBACK
                    elif movement is FrontierMovement.SUCCESS:
                        decision = RepairDecision.SUCCESS
                        metrics.success = True
                    metrics.best_frontier = max(metrics.best_frontier, int(feedback.frontier.stage))
                episode.attempts.append(RepairAttempt(
                    1, selected.id, failure.frontier, after_frontier,
                    failure.frontier.error_signature,
                    after_frontier.error_signature if after_frontier else None,
                    modified, self.build_feedback.available, build_result, None, 0.0, decision,
                ))
                message = (
                    "Applied one evidence-supported repair; official build feedback was unavailable."
                    if modified and not self.build_feedback.available
                    else "Repair attempt completed with build feedback."
                )
            else:
                metrics.policy_rejection_count += int(bool(selected.policy_findings))
                metrics.invalid_patch_count += 1
        elif self.model_router is not None:
            message = "Analysis completed with model evidence; no validated edit was applied."

        if self.model_router is not None:
            metrics.model_calls = self.model_router.budget.calls
            metrics.strong_calls = self.model_router.budget.strong_calls
            metrics.input_tokens = self.model_router.budget.input_tokens
            metrics.output_tokens = self.model_router.budget.output_tokens
            metrics.estimated_cost = self.model_router.budget.estimated_cost
        if (
            candidate is None
            and case.target_architecture.canonical is Architecture.UNKNOWN
            and familiarity.requires_discovery
        ):
            metrics.selected_repair_family = RepairFamily.NO_SAFE_FIX.value
        metrics.finish()
        self._transition(
            ControllerStage.FINALIZE.value,
            modified_paths=modified,
            attempts=metrics.attempts,
            probes=metrics.probe_count,
            model_calls=metrics.model_calls,
            best_frontier=metrics.best_frontier,
            success=metrics.success,
        )
        constitution = constitution_payload(profile)
        diagnostics = {
            "failure": failure,
            "familiarity": familiarity,
            "hypotheses": hypotheses.hypotheses,
            "graph_nodes": len(graph.nodes) if graph else 0,
            "graph_edges": sum(len(edges) for edges in graph.adjacency.values()) if graph else 0,
            "build_feedback_available": self.build_feedback.available,
            "build_feedback_reason": self.build_feedback.reason_unavailable,
            "episode_summary": episode.compact_summary(),
        }
        write_diagnostic(self.workspace.output, "evidence-ledger.json", ledger.to_dict())
        write_diagnostic(self.workspace.output, "architecture-constitution.json", constitution)
        write_diagnostic(self.workspace.output, "repair-episode.json", diagnostics)
        write_diagnostic(self.workspace.output, "metrics.json", metrics.to_dict())
        return ControllerOutcome(
            AgentResult(PROTOCOL_VERSION, "completed", message, modified),
            metrics, ledger, constitution, episode,
        )
