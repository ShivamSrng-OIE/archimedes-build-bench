"""Understandable evidence-first state machine for one bounded repair case."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from enum import Enum

from ..architecture.constitution import constitution_payload
from ..architecture.discovery import discover_from_static_evidence
from ..architecture.familiarity import assess_familiarity
from ..architecture.hazard_scanner import ArchitectureHazard, scan_architecture_hazards
from ..architecture.profile import construct_profile
from ..architecture.semantic_anchor import extract_anchor, validate_anchor
from ..build.feedback import BuildBudget, BuildFeedbackProvider, BuildFeedbackResult
from ..config import ContextMode, ModelBackend, RuntimeConfig
from ..constants import MAX_TEXT_FILE_BYTES, PROTOCOL_VERSION, RELIABILITY
from ..context.repository_search import (
    SearchSnippet,
    inventory_repository,
    search_regex,
)
from ..context.snippet_selector import select_snippets
from ..evidence.confidence import claim_justifies_patch
from ..evidence.ledger import EvidenceLedger
from ..errors import (
    BuildFeedbackError,
    BudgetExhaustedError,
    ModelError,
    PatchValidationError,
    PathPolicyError,
)
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
from ..repair.critic import portability_findings, passes_portability_critic
from ..repair.deterministic import match_deterministic_repair
from ..repair.executor import EditTransaction, apply_candidate, rollback_transaction
from ..repair.planner import (
    conditional_flag_candidate,
    deterministic_match_candidate,
    model_plan_candidate,
)
from ..repair.policy_critic import policy_findings, passes_policy_critic
from ..result_writer import write_diagnostic
from ..schemas import (
    AgentResult,
    Architecture,
    ConsequenceLevel,
    EvidenceItem,
    EvidenceKind,
    FailureAnalysis,
    HypothesisStatus,
    JSONValue,
    PatchCandidate,
    Probe,
    RepairAttempt,
    RepairDecision,
    RepairFamily,
    SemanticAnchor,
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

    @staticmethod
    def _latest_failure_evidence(ledger: EvidenceLedger) -> EvidenceItem | None:
        return next(
            (
                item
                for item in reversed(list(ledger.evidence.values()))
                if item.kind
                in {
                    EvidenceKind.BUILD_LOG,
                    EvidenceKind.COMPILER_OUTPUT,
                    EvidenceKind.LINKER_OUTPUT,
                    EvidenceKind.TOOL_OUTPUT,
                }
            ),
            None,
        )

    def _seed_ledger(self, case: object, failure: object) -> EvidenceLedger:
        from ..schemas import CaseData, FailureAnalysis

        assert isinstance(case, CaseData) and isinstance(failure, FailureAnalysis)
        ledger = EvidenceLedger()
        architecture_known = (
            case.target_architecture.canonical is not Architecture.UNKNOWN
        )
        ledger.add_evidence(
            kind=EvidenceKind.PROVIDED_METADATA,
            summary=(
                f"target architecture is {case.target_architecture.raw}"
                if architecture_known
                else "target architecture metadata is unknown"
            ),
            reliability=RELIABILITY["provided_metadata"]
            if architecture_known
            else RELIABILITY["guess"],
            raw_excerpt=case.target_architecture.raw,
            source="case metadata",
            stage=ControllerStage.CASE_INGESTION.value,
        )
        if failure.primary_diagnostic:
            item = ledger.add_evidence(
                kind=EvidenceKind.COMPILER_OUTPUT
                if failure.family.value == "compilation"
                else EvidenceKind.BUILD_LOG,
                summary="first causal build diagnostic",
                reliability=RELIABILITY["compiler_diagnostic"],
                raw_excerpt=failure.primary_diagnostic,
                source="initial build log",
                stage=ControllerStage.FORENSICS.value,
            )
            failure.evidence_ids.append(item.id)
        return ledger

    def _record_hazards(
        self, ledger: EvidenceLedger, hazards: list[ArchitectureHazard]
    ) -> None:
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

    def _semantic_anchors(
        self, ledger: EvidenceLedger, hazards: list[ArchitectureHazard]
    ) -> list[SemanticAnchor]:
        """Create semantic-preservation questions only from directly observed hazards."""
        if not self.config.features.semantic_anchors:
            return []
        anchors: list[SemanticAnchor] = []
        for hazard in hazards:
            evidence_ids = [
                item.id
                for item in ledger.evidence.values()
                if item.path == hazard.path and item.line_start == hazard.line
            ]
            anchor = extract_anchor(hazard, evidence_ids=evidence_ids)
            if anchor is not None:
                anchors.append(anchor)
        self._trace(
            ControllerStage.ARCHITECTURE.value,
            "semantic_anchors_extracted",
            count=len(anchors),
            validated=sum(not validate_anchor(item) for item in anchors),
            uncertain=sum(bool(validate_anchor(item)) for item in anchors),
        )
        return anchors

    def _flag_candidate(
        self,
        case: object,
        failure: object,
        hazards: list[ArchitectureHazard],
        anchors: list[SemanticAnchor],
        ledger: EvidenceLedger,
    ) -> PatchCandidate | None:
        from ..schemas import CaseData, FailureAnalysis

        assert isinstance(case, CaseData) and isinstance(failure, FailureAnalysis)
        if case.target_architecture.canonical in {
            Architecture.UNKNOWN,
            Architecture.X86_64,
        }:
            return None
        diagnostic = failure.primary_diagnostic.lower()
        matching_hazards = [
            item
            for item in hazards
            if item.category == "compiler_flag"
            and item.matched_text.lower() in diagnostic
            and re.search(r"-m(?:sse|avx)|-march=x86", item.matched_text, re.I)
        ]
        if len(matching_hazards) != 1:
            return None
        hazard = matching_hazards[0]
        anchor = next(
            (
                item
                for item in anchors
                if item.site == f"{hazard.path}:{hazard.line}"
                and item.symbol == hazard.matched_text
            ),
            None,
        )
        if self.config.features.semantic_anchors and (
            anchor is None or validate_anchor(anchor)
        ):
            return None
        file_evidence = next(
            item
            for item in ledger.evidence.values()
            if item.path == hazard.path and item.line_start == hazard.line
        )
        log_evidence = self._latest_failure_evidence(ledger)
        if log_evidence is None:
            return None
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
            supporting_evidence_ids=(
                list(anchor.evidence_ids) if anchor is not None else [file_evidence.id]
            ),
            consequence=ConsequenceLevel.HIGH,
        )
        claims = [origin, rejected, optional]
        if self.config.features.claim_confidence and not all(
            claim_justifies_patch(item.confidence, item.consequence_level, item.status)
            for item in claims
        ):
            return None
        confidence = (
            min(item.confidence for item in claims)
            if self.config.features.claim_confidence
            else 0.80
        )
        return conditional_flag_candidate(
            case, hazard, claim_ids=[item.id for item in claims], confidence=confidence
        )

    def _deterministic_rule_candidate(
        self, *, case: object, failure: object, ledger: EvidenceLedger
    ) -> PatchCandidate | None:
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
        log_evidence = self._latest_failure_evidence(ledger)
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
        confidence = (
            claim.confidence
            if self.config.features.claim_confidence
            else match.confidence
        )
        return deterministic_match_candidate(
            match,
            claim_ids=[claim.id],
            confidence=confidence,
            current_frontier=failure.frontier.stage,
        )

    def _model_file_context(self, failure: object, recommended_files: object) -> str:
        """Read only a small validated set of model-recommended or causal files."""
        from ..schemas import FailureAnalysis

        assert isinstance(failure, FailureAnalysis)
        requested: list[str] = []
        if failure.frontier.primary_file:
            requested.append(failure.frontier.primary_file)
        if isinstance(recommended_files, list):
            requested.extend(
                value for value in recommended_files if isinstance(value, str)
            )
        inventory = inventory_repository(self.workspace.repo)
        requested.extend(inventory.build_files[:4])
        packaging = sorted(
            inventory.packaging_files,
            key=lambda path: (
                0
                if path == "debian/rules"
                else 1
                if path == "debian/control"
                else 2
                if path.endswith((".install", ".symbols", ".links", ".dirs"))
                else 3,
                path,
            ),
        )
        requested.extend(packaging[:4])
        snippets: list[SearchSnippet] = []
        for relative in dict.fromkeys(requested):
            if len(snippets) >= 8 or any(
                part.startswith(".") for part in relative.replace("\\", "/").split("/")
            ):
                continue
            try:
                path = self.path_policy.validate_relative(relative)
                if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                    continue
                data = path.read_bytes()
            except (OSError, UnicodeError, PathPolicyError):
                continue
            if b"\0" in data[:8192]:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeError:
                continue
            lines = text.splitlines()
            line_start = 1
            line_end = min(160, len(lines))
            if relative == failure.frontier.primary_file:
                escaped = re.escape(relative).replace(r"/", r"[\\/]")
                location = re.search(
                    rf"(?:^|[\s'\"])(?:\./)?{escaped}:(?P<line>[1-9][0-9]*)",
                    failure.primary_diagnostic,
                )
                if location is not None:
                    causal_line = int(location.group("line"))
                    line_start = max(1, causal_line - 79)
                    line_end = min(len(lines), causal_line + 80)
            snippets.append(
                SearchSnippet(
                    relative,
                    line_start,
                    line_end,
                    "\n".join(lines[line_start - 1 : line_end]),
                    "validated causal/model-selected file",
                    1.0,
                )
            )
        return select_snippets(snippets, max_characters=24_000)

    def _model_causal_findings(self, failure: object, candidate: object) -> list[str]:
        """Reject plans that add an alternative but leave a diagnosed flag active."""
        from ..schemas import FailureAnalysis, PatchCandidate

        assert isinstance(failure, FailureAnalysis) and isinstance(
            candidate, PatchCandidate
        )
        options = list(
            dict.fromkeys(
                re.findall(
                    r"(?<![A-Za-z0-9_])-(?:m(?:sse[0-9.]*|avx[0-9.]*|neon)\b|"
                    r"(?:march|mcpu|mtune|mabi|mfpu)=[^\s'\"`]+)",
                    failure.primary_diagnostic,
                    flags=re.I,
                )
            )
        )
        findings: list[str] = []
        guard_markers = ("ifneq", "ifeq", "if(", "#if", "check_c_compiler_flag")
        target_markers = (
            "-dumpmachine",
            "check_c_compiler_flag",
            "cmake_system_processor",
            "target_compile_options",
            "%ifarch",
            "__x86_64__",
            "__amd64__",
        )
        for option in options:
            matches = search_regex(
                self.workspace.repo,
                re.escape(option),
                reason="diagnosed compiler-option provenance",
                max_results=20,
            )
            for match in matches:
                replacements = [
                    edit
                    for edit in candidate.planned_edits
                    if edit.path == match.path
                    and edit.operation == "replace_lines"
                    and isinstance(edit.expected_text, str)
                    and option.lower() in edit.expected_text.lower()
                ]
                if not replacements:
                    findings.append(
                        f"plan leaves diagnosed option {option} active at "
                        f"{match.path}:{match.line_start}"
                    )
                    continue
                for edit in replacements:
                    replacement = edit.replacement.lower()
                    if option.lower() in replacement and not any(
                        marker in replacement for marker in guard_markers
                    ):
                        findings.append(
                            f"replacement keeps {option} without a target or capability guard"
                        )
                    elif option.lower() in replacement and not any(
                        marker in replacement for marker in target_markers
                    ):
                        findings.append(
                            f"guard for {option} is not derived from compiler-target or capability evidence"
                        )
                    elif option.lower() in replacement and re.search(
                        r"\$\(\s*filter\s+(?:x86_64|amd64)\s*,", replacement
                    ):
                        findings.append(
                            f"guard for {option} compares a full compiler target triple to an "
                            "exact architecture name; use an architecture-prefix wildcard"
                        )
        return findings

    def _consult_model_if_configured(
        self, case: object, failure: object, ledger: EvidenceLedger
    ) -> PatchCandidate | None:
        if self.model_router is None:
            return None
        from ..schemas import CaseData, FailureAnalysis

        assert isinstance(case, CaseData) and isinstance(failure, FailureAnalysis)
        selected_context = (
            f"source_architecture={case.source_architecture.raw}\n"
            f"target_architecture={case.target_architecture.raw}\n"
            f"first_diagnostic={failure.primary_diagnostic}\n"
            f"causal_log_context:\n{failure.frontier.log_excerpt}\n"
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
        model_claim_added = False
        if isinstance(claims, list):
            for value in claims[:10]:
                if isinstance(value, dict) and isinstance(value.get("statement"), str):
                    statement = value.get("statement")
                    assert isinstance(statement, str)
                    ledger.add_claim(
                        statement,
                        "llm_inference",
                        supporting_evidence_ids=[evidence.id],
                        consequence=ConsequenceLevel.MEDIUM,
                    )
                    model_claim_added = True
        if self.model_router.records:
            self.model_router.records[-1].changed_hypothesis = model_claim_added
        context = self._model_file_context(
            failure, response.data.get("recommended_files")
        )
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
            "observed portable path. Every non-create edit must include expected_text exactly as shown. "
            "Modify the existing causal declaration; do not add a guarded duplicate while leaving "
            "the diagnosed declaration active. For Makefiles use $(CC) -dumpmachine, not uname or "
            "undeclared case metadata, to identify the compilation target, and match its full "
            "triple with an architecture prefix wildcard such as x86_64%.\n"
            f"{context}"
        )
        plan_response = None
        candidate = None
        for plan_attempt in range(1, 3):
            plan_response = self.model_router.complete(
                task=ModelTask.PATCH_PLANNING,
                system_prompt=system_prompt(ModelTask.PATCH_PLANNING),
                user_prompt=plan_prompt,
                signals=EscalationSignals(
                    ood_without_evidence=case.target_architecture.canonical
                    is Architecture.UNKNOWN
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
                plan_attempt=plan_attempt,
            )
            candidate = model_plan_candidate(
                plan_response.data,
                path_policy=self.path_policy,
                current_frontier=failure.frontier.stage,
            )
            if candidate is None:
                findings = ["structured plan failed deterministic validation"]
            else:
                findings = sorted(
                    set(
                        [
                            *self._model_causal_findings(failure, candidate),
                            *portability_findings(candidate),
                            *policy_findings(candidate, self.path_policy),
                        ]
                    )
                )
            if not findings:
                break
            self._trace(
                ControllerStage.REPAIR.value,
                "model_patch_rejected",
                reason="; ".join(findings),
                plan_attempt=plan_attempt,
            )
            candidate = None
            if plan_attempt == 1:
                plan_prompt += (
                    "\nThe previous plan was deterministically rejected: "
                    + "; ".join(findings)
                    + ". Return a different plan that directly removes or guards the existing "
                    "causal declaration."
                )
        if candidate is None or plan_response is None:
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
        log_evidence = self._latest_failure_evidence(ledger)
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
        dependent_claims = [
            causal,
            *(ledger.claims[item_id] for item_id in assumption_ids),
        ]
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
        return candidate

    def _mark_model_patch_applied(self, candidate_id: str) -> None:
        """Attribute a model patch only after every critic accepts and the edit is applied."""
        if self.model_router is None or not candidate_id.startswith("candidate-model-"):
            return
        record = next(
            (
                item
                for item in reversed(self.model_router.records)
                if item.task is ModelTask.PATCH_PLANNING
            ),
            None,
        )
        if record is not None:
            record.caused_patch = True

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
        if (
            not self.config.features.active_probes
            or "atomic" not in failure.primary_diagnostic.lower()
        ):
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

    def _candidate_for(
        self,
        *,
        case: object,
        failure: object,
        hazards: list[ArchitectureHazard],
        anchors: list[SemanticAnchor],
        ledger: EvidenceLedger,
        excluded_families: set[RepairFamily] | None = None,
    ) -> PatchCandidate | None:
        excluded = excluded_families or set()
        candidate = None
        if self.config.features.repair_rules:
            candidate = self._deterministic_rule_candidate(
                case=case, failure=failure, ledger=ledger
            )
            if candidate is not None and candidate.repair_family in excluded:
                candidate = None
            if candidate is None:
                candidate = self._flag_candidate(
                    case, failure, hazards, anchors, ledger
                )
                if candidate is not None and candidate.repair_family in excluded:
                    candidate = None
        if (
            candidate is not None
            and self.model_router is not None
            and self.config.model_backend is ModelBackend.HOSTED_OPENAI_COMPATIBLE
        ):
            from ..schemas import CaseData, FailureAnalysis

            assert isinstance(case, CaseData) and isinstance(failure, FailureAnalysis)
            try:
                critique = self.model_router.complete(
                    task=ModelTask.PATCH_CRITIQUE,
                    system_prompt=system_prompt(ModelTask.PATCH_CRITIQUE),
                    user_prompt=(
                        f"source_architecture={case.source_architecture.raw}\n"
                        f"target_architecture={case.target_architecture.raw}\n"
                        f"first_diagnostic={failure.primary_diagnostic}\n"
                        f"repair_family={candidate.repair_family.value}\n"
                        f"rationale={candidate.rationale}\n"
                        + "\n".join(
                            f"path={edit.path}\nexpected_text={edit.expected_text}\n"
                            f"replacement={edit.replacement}"
                            for edit in candidate.planned_edits
                        )
                    ),
                )
            except (ModelError, BudgetExhaustedError) as exc:
                # A transient hosted-model failure must not discard an already
                # validated deterministic repair; keep it and make no new calls.
                self._trace(
                    ControllerStage.REPAIR.value,
                    "model_critique_unavailable",
                    error_type=type(exc).__name__,
                )
                critique = None
            if critique is not None:
                critique_findings = critique.data["findings"]
                assert isinstance(critique_findings, list)
                self._trace(
                    ControllerStage.REPAIR.value,
                    "model_critique_completed",
                    accepted=critique.data["accepted"],
                    finding_count=len(critique_findings),
                )
                if not critique.data["accepted"]:
                    deterministic = candidate
                    alternative = None
                    try:
                        alternative = self._consult_model_if_configured(
                            case, failure, ledger
                        )
                    except (ModelError, BudgetExhaustedError) as exc:
                        self._trace(
                            ControllerStage.REPAIR.value,
                            "model_unavailable",
                            error_type=type(exc).__name__,
                        )
                    if alternative is not None and alternative.repair_family not in excluded:
                        candidate = alternative
                    else:
                        # No usable model alternative: the deterministic rule
                        # candidate remains the best-supported repair.
                        candidate = deterministic
                        self._trace(
                            ControllerStage.REPAIR.value,
                            "model_critique_rejection_overridden",
                            candidate_id=deterministic.id,
                            reason="no admissible model alternative",
                        )
        if candidate is None and self.model_router is not None:
            try:
                candidate = self._consult_model_if_configured(case, failure, ledger)
            except (ModelError, BudgetExhaustedError) as exc:
                if self.config.model_backend is ModelBackend.HOSTED_OPENAI_COMPATIBLE:
                    raise
                self._trace(
                    ControllerStage.REPAIR.value,
                    "model_unavailable",
                    error_type=type(exc).__name__,
                )
                candidate = None
            if candidate is not None and candidate.repair_family in excluded:
                self._trace(
                    ControllerStage.REPAIR.value,
                    "candidate_rejected",
                    candidate_id=candidate.id,
                    reason="repair family already contradicted by build feedback",
                )
                candidate = None
        self._trace(
            ControllerStage.REPAIR.value,
            "candidate_generation_completed",
            candidate_id=(candidate.id if candidate else None),
            repair_family=(candidate.repair_family.value if candidate else None),
            confidence=(candidate.confidence if candidate else None),
            excluded_families=sorted(item.value for item in excluded),
        )
        return candidate

    def _feedback_failure(
        self, feedback: object, ledger: EvidenceLedger
    ) -> FailureAnalysis:
        assert isinstance(feedback, BuildFeedbackResult)
        failure = analyze_build_log(feedback.raw_log)
        failure.frontier = feedback.frontier
        kind = {
            "compilation": EvidenceKind.COMPILER_OUTPUT,
            "linking": EvidenceKind.LINKER_OUTPUT,
        }.get(failure.family.value, EvidenceKind.BUILD_LOG)
        item = ledger.add_evidence(
            kind=kind,
            summary="first causal diagnostic from a repair build experiment",
            reliability=RELIABILITY["tool_result"],
            raw_excerpt=failure.primary_diagnostic,
            source=f"build feedback {feedback.request_id or 'unidentified'}",
            stage=ControllerStage.BUILD.value,
        )
        failure.evidence_ids.append(item.id)
        return failure

    def _mark_model_patch_outcome(
        self, candidate_id: str, movement: FrontierMovement
    ) -> None:
        if self.model_router is None or not candidate_id.startswith("candidate-model-"):
            return
        record = next(
            (item for item in reversed(self.model_router.records) if item.caused_patch),
            None,
        )
        if record is not None:
            record.improved_frontier = movement in {
                FrontierMovement.PROGRESS,
                FrontierMovement.PARTIAL_PROGRESS,
                FrontierMovement.SUCCESS,
            }

    def _compare_build_feedback(
        self, before: object, after: object, *, success: bool
    ) -> FrontierMovement:
        """Compare experiments with or without the ordered-frontier research component."""
        from ..schemas import FailureFrontier

        assert isinstance(before, FailureFrontier) and isinstance(
            after, FailureFrontier
        )
        if success:
            return FrontierMovement.SUCCESS
        if self.config.features.failure_frontier:
            return compare_frontiers(before, after)
        if after.error_signature == before.error_signature:
            return FrontierMovement.INEFFECTIVE
        return FrontierMovement.PARTIAL_PROGRESS

    def run(self) -> ControllerOutcome:
        """Run deterministic analysis, evidence-gated repair, and optional build experiment."""
        case = load_case(self.workspace)
        self.path_policy = PathPolicy.from_metadata(self.workspace.repo, case.metadata)
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
        metrics = CaseMetrics(
            case.case_id, case.source_architecture.raw, case.target_architecture.raw
        )
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
        metrics.initial_frontier = (
            int(failure.frontier.stage) if self.config.features.failure_frontier else -1
        )
        metrics.best_frontier = metrics.initial_frontier
        ledger = self._seed_ledger(case, failure)
        log_event(
            self.logger,
            "forensics",
            "primary failure identified",
            frontier=failure.frontier.stage.name.lower(),
            confidence=failure.frontier.confidence,
        )

        hazards = (
            scan_architecture_hazards(self.workspace.repo)
            if self.config.features.deterministic_forensics
            else []
        )
        self._trace(
            ControllerStage.ARCHITECTURE.value,
            "hazards_scanned",
            count=len(hazards),
            categories=sorted({hazard.category for hazard in hazards}),
        )
        self._record_hazards(ledger, hazards)
        anchors = self._semantic_anchors(ledger, hazards)
        metrics.semantic_anchor_count = len(anchors)
        profile = construct_profile(
            case.target_architecture, list(ledger.evidence.values())
        )
        familiarity = assess_familiarity(
            profile,
            repository_coverage=min(1.0, len(hazards) / 5),
            portable_rule_coverage=0.8 if hazards else 0.0,
        )
        if (
            familiarity.requires_discovery
            and self.config.features.architecture_discovery
        ):
            discover_from_static_evidence(profile, case, hazards)
        metrics.familiarity_score = familiarity.score
        self._transition(
            ControllerStage.ARCHITECTURE.value,
            familiarity_score=familiarity.score,
            familiarity_band=familiarity.band,
            discovery_required=familiarity.requires_discovery,
            ood_signals=profile.ood_signals,
        )
        log_event(
            self.logger,
            "architecture",
            "familiarity assessed",
            target=case.target_architecture.raw,
            score=familiarity.score,
            discovery=familiarity.requires_discovery,
        )

        graph = (
            build_delta_graph(case, failure, hazards)
            if self.config.features.delta_graph
            else None
        )
        hypotheses = generate_initial_hypotheses(failure, hazards)
        self._transition(
            ControllerStage.HYPOTHESES.value,
            hypothesis_count=len(hypotheses.hypotheses),
            top_hypothesis=(
                hypotheses.ranked()[0].id if hypotheses.hypotheses else None
            ),
            graph_nodes=(len(graph.nodes) if graph else 0),
            graph_edges=(
                sum(len(edges) for edges in graph.adjacency.values()) if graph else 0
            ),
        )
        episode.hypotheses = hypotheses.ranked()
        metrics.hypothesis_count = len(hypotheses.hypotheses)
        self._run_cheap_probe(failure=failure, episode=episode, ledger=ledger)
        metrics.probe_count = len(episode.probes)
        episode.observations.extend(
            (
                f"files={len(inventory.files)} build_files={len(inventory.build_files)}",
                f"failure={failure.primary_diagnostic}",
                f"hazards={len(hazards)} familiarity={familiarity.score}",
            )
        )

        current_failure = failure
        current_hazards = hazards
        candidate = self._candidate_for(
            case=case,
            failure=current_failure,
            hazards=current_hazards,
            anchors=anchors,
            ledger=ledger,
        )
        contradicted_families: set[RepairFamily] = set()
        retained_transactions: list[EditTransaction] = []
        modified: list[str] = []
        message = (
            "Analysis completed; no evidence-supported safe repair was identified."
        )
        while (
            candidate is not None and metrics.attempts < self.config.max_repair_attempts
        ):
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
            if not portability_accepted or not policy_accepted:
                metrics.policy_rejection_count += int(bool(selected.policy_findings))
                metrics.invalid_patch_count += 1
                break
            self._transition(
                ControllerStage.REPAIR.value,
                candidate_id=selected.id,
                repair_family=selected.repair_family.value,
                attempt=metrics.attempts + 1,
            )
            before_frontier = current_failure.frontier
            before_signature = current_failure.frontier.error_signature
            try:
                transaction = apply_candidate(selected, self.path_policy)
            except PatchValidationError as exc:
                metrics.invalid_patch_count += 1
                message = "Candidate failed an atomic edit precondition; no partial repair was retained."
                self._trace(
                    ControllerStage.REPAIR.value,
                    "candidate_application_failed",
                    candidate_id=selected.id,
                    error_type=type(exc).__name__,
                )
                break
            self._mark_model_patch_applied(selected.id)
            attempted_paths = sorted(edit.relative_path for edit in transaction.edits)
            self._trace(
                ControllerStage.REPAIR.value,
                "candidate_applied",
                candidate_id=selected.id,
                modified_paths=attempted_paths,
                edit_count=len(transaction.edits),
                attempt=metrics.attempts + 1,
            )
            metrics.attempts += 1
            metrics.critical_claim_confidence = selected.confidence
            metrics.selected_repair_family = selected.repair_family.value
            decision = RepairDecision.KEEP
            after_frontier = None
            build_result = None
            build_requested = False
            movement: FrontierMovement | None = None
            build_feedback_error: str | None = None
            should_continue = False
            should_retain = True
            if (
                self.build_feedback.available
                and selected.expected_information_gain_if_built > 0.20
            ):
                self._transition(
                    ControllerStage.BUILD.value,
                    candidate_id=selected.id,
                    expected_information_value=selected.expected_information_gain_if_built,
                    attempt=metrics.attempts,
                )
                build_requested = True
                metrics.build_requests += 1
                try:
                    self.build_budget.authorize()
                    feedback = self.build_feedback.request(
                        expected_information_value=selected.expected_information_gain_if_built
                    )
                    self.build_budget.record()
                except (BuildFeedbackError, BudgetExhaustedError) as exc:
                    build_feedback_error = type(exc).__name__
                    build_result = "unavailable"
                    self._trace(
                        ControllerStage.BUILD.value,
                        "build_feedback_failed",
                        error_type=build_feedback_error,
                        attempt=metrics.attempts,
                    )
                else:
                    after_frontier = feedback.frontier
                    movement = self._compare_build_feedback(
                        current_failure.frontier,
                        feedback.frontier,
                        success=feedback.success,
                    )
                    build_result = movement.value
                    self._mark_model_patch_outcome(selected.id, movement)
                    self._trace(
                        ControllerStage.BUILD.value,
                        "build_feedback_received",
                        movement=movement.value,
                        new_frontier=feedback.frontier.stage.name.lower(),
                        success=feedback.success,
                        attempt=metrics.attempts,
                    )
                    if self.config.features.failure_frontier:
                        metrics.best_frontier = max(
                            metrics.best_frontier, int(feedback.frontier.stage)
                        )
                    if movement in {
                        FrontierMovement.REGRESSION,
                        FrontierMovement.INEFFECTIVE,
                    }:
                        rollback_transaction(transaction)
                        metrics.rollback_count += 1
                        decision = RepairDecision.ROLLBACK
                        should_retain = False
                        should_continue = True
                        contradicted_families.add(selected.repair_family)
                        rejected = ledger.add_evidence(
                            kind=EvidenceKind.REPAIR_HISTORY,
                            summary=f"repair attempt produced {movement.value} build feedback",
                            reliability=RELIABILITY["tool_result"],
                            raw_excerpt=feedback.frontier.error_signature,
                            source=f"build feedback {feedback.request_id or 'unidentified'}",
                            stage=ControllerStage.BUILD.value,
                        )
                        episode.contradictions.append(
                            f"{selected.id} contradicted by {rejected.id}: {movement.value}"
                        )
                        matching_hypothesis = next(
                            (
                                item
                                for item in hypotheses.hypotheses
                                if item.repair_family is selected.repair_family
                                and item.status is not HypothesisStatus.FALSIFIED
                            ),
                            None,
                        )
                        if matching_hypothesis is not None:
                            hypotheses.falsify(matching_hypothesis.id)
                            metrics.hypotheses_falsified += 1
                    elif movement is FrontierMovement.SUCCESS:
                        decision = RepairDecision.SUCCESS
                        metrics.success = True
                        ledger.add_evidence(
                            kind=EvidenceKind.TOOL_OUTPUT,
                            summary="repair build completed successfully",
                            reliability=RELIABILITY["tool_result"],
                            raw_excerpt=feedback.frontier.error_signature,
                            source=f"build feedback {feedback.request_id or 'unidentified'}",
                            stage=ControllerStage.BUILD.value,
                        )
                        for item in hypotheses.hypotheses:
                            if item.repair_family is selected.repair_family:
                                item.status = HypothesisStatus.SELECTED
                    else:
                        current_failure = self._feedback_failure(feedback, ledger)
                        episode.observations.append(
                            f"attempt={metrics.attempts} movement={movement.value} "
                            f"frontier={feedback.frontier.stage.name.lower()}"
                        )
                        current_hazards = (
                            scan_architecture_hazards(self.workspace.repo)
                            if self.config.features.deterministic_forensics
                            else []
                        )
                        anchors = self._semantic_anchors(ledger, current_hazards)
                        metrics.semantic_anchor_count = max(
                            metrics.semantic_anchor_count, len(anchors)
                        )
                        for item in hypotheses.hypotheses:
                            if item.repair_family is selected.repair_family:
                                item.status = HypothesisStatus.SUPPORTED
                        should_continue = True
            if should_retain:
                retained_transactions.append(transaction)
            episode.attempts.append(
                RepairAttempt(
                    metrics.attempts,
                    selected.id,
                    before_frontier,
                    after_frontier,
                    before_signature,
                    after_frontier.error_signature if after_frontier else None,
                    attempted_paths,
                    build_requested,
                    build_result,
                    None,
                    0.0,
                    decision,
                )
            )
            if not self.build_feedback.available:
                message = "Applied one evidence-supported repair; official build feedback was unavailable."
                break
            if build_feedback_error is not None:
                message = (
                    "Applied one evidence-supported repair; official build feedback failed safely "
                    "and the repair was preserved."
                )
                break
            if not build_requested:
                message = (
                    "Applied one evidence-supported repair; build feedback was skipped because "
                    "its expected information value was below the request threshold."
                )
                break
            if metrics.success:
                message = (
                    f"Verified repair success after {metrics.attempts} attempt(s)."
                )
                break
            if not should_continue:
                message = f"Repair attempt {metrics.attempts} was rolled back after {build_result}."
                break
            if metrics.attempts >= self.config.max_repair_attempts:
                message = (
                    "Preserved the best progressing repair state at the attempt limit."
                )
                break
            if self.build_budget.remaining == 0:
                message = "Preserved the best progressing repair state at the build-request limit."
                break
            candidate = self._candidate_for(
                case=case,
                failure=current_failure,
                hazards=current_hazards,
                anchors=anchors,
                ledger=ledger,
                excluded_families=contradicted_families,
            )
            if candidate is None:
                message = "Preserved repair progress; no further evidence-supported candidate was found."
                break
        if (
            candidate is None
            and self.model_router is not None
            and self.model_router.records
            and metrics.attempts == 0
        ):
            message = (
                "Analysis completed with model evidence; no validated edit was applied."
            )

        modified = sorted(
            {
                edit.relative_path
                for transaction in retained_transactions
                if transaction.committed
                for edit in transaction.edits
            }
        )
        metrics.modified_files = len(modified)
        metrics.patch_lines = sum(
            edit.changed_line_end - edit.changed_line_start + 1
            for transaction in retained_transactions
            if transaction.committed
            for edit in transaction.edits
        )
        metrics.causal_file_identified = bool(modified)

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
            "graph_edges": sum(len(edges) for edges in graph.adjacency.values())
            if graph
            else 0,
            "semantic_anchors": [asdict(item) for item in anchors],
            "feature_flags": asdict(self.config.features),
            "build_feedback_available": self.build_feedback.available,
            "build_feedback_reason": self.build_feedback.reason_unavailable,
            "episode_summary": episode.compact_summary(),
            "model_call_records": (
                [asdict(item) for item in self.model_router.records]
                if self.model_router is not None
                else []
            ),
        }
        write_diagnostic(
            self.workspace.output, "evidence-ledger.json", ledger.to_dict()
        )
        write_diagnostic(
            self.workspace.output, "architecture-constitution.json", constitution
        )
        write_diagnostic(self.workspace.output, "repair-episode.json", diagnostics)
        write_diagnostic(self.workspace.output, "metrics.json", metrics.to_dict())
        return ControllerOutcome(
            AgentResult(PROTOCOL_VERSION, "completed", message, modified),
            metrics,
            ledger,
            constitution,
            episode,
        )
