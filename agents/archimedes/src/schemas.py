"""Shared dataclasses and enums that every Archimedes subsystem uses.

These types are the common vocabulary of a run: Case data, evidence, claims,
hypotheses, failure frontiers, repair candidates and the final agent result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import TypeAlias

JSONValue: TypeAlias = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)


class Architecture(str, Enum):
    """Canonical CPU architecture names after alias resolution.

    Raw names such as ``amd64`` or ``arm64`` are mapped onto these members by
    ``architecture.aliases``. ``UNKNOWN`` means the name was missing or not
    recognised; the Agent never guesses a concrete architecture in that case.
    """

    X86_64 = "x86_64"
    AARCH64 = "aarch64"
    RISCV64 = "riscv64"
    UNKNOWN = "unknown"


class EvidenceKind(str, Enum):
    """Where an evidence item came from.

    The kind decides how confidence fusion treats the item. ``LLM_INFERENCE``
    and ``MODEL_SELF_CONSISTENCY`` are model output, so a claim backed only by
    them is capped at 0.65; ``STATIC_ANALYSIS`` is treated as a heuristic and a
    claim backed only by it is capped at 0.75. The other members name direct
    sources such as Case metadata, a file read, the build log, compiler or
    linker output, repository layout, cross-architecture comparison, curated
    portability knowledge, and the outcome of an earlier repair attempt.
    """

    PROVIDED_METADATA = "provided_metadata"
    DIRECT_FILE_OBSERVATION = "direct_file_observation"
    BUILD_LOG = "build_log"
    TOOL_OUTPUT = "tool_output"
    COMPILER_OUTPUT = "compiler_output"
    LINKER_OUTPUT = "linker_output"
    STATIC_ANALYSIS = "static_analysis"
    REPOSITORY_STRUCTURE = "repository_structure"
    CROSS_ARCH_COMPARISON = "cross_arch_comparison"
    AUTHORITATIVE_KNOWLEDGE = "authoritative_knowledge"
    REPAIR_HISTORY = "repair_history"
    LLM_INFERENCE = "llm_inference"
    MODEL_SELF_CONSISTENCY = "model_self_consistency"


class ConfidenceBand(str, Enum):
    """Coarse label for a confidence value, used in traces and the ledger.

    ``evidence.confidence.confidence_band`` assigns the bands: below 0.20 is
    ``VERY_LOW``, below 0.40 ``LOW``, below 0.70 ``MODERATE``, below 0.90
    ``HIGH``, and 0.90 or above ``VERY_HIGH``.
    """

    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ClaimStatus(str, Enum):
    """Where a claim stands after its evidence has been fused.

    ``OPEN`` has no supporting evidence yet, ``SUPPORTED`` has some,
    ``CONFIRMED`` reached at least 0.90 with non-model support, and
    ``CONTRADICTED`` has strong contradicting evidence that outweighs its
    support; a contradicted claim never justifies a patch. ``RETIRED`` marks a
    claim that is no longer relevant and is not set by the current fusion code.
    """

    OPEN = "open"
    SUPPORTED = "supported"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    RETIRED = "retired"


class ConsequenceLevel(str, Enum):
    """How much damage acting on a wrong claim could do.

    The level sets the confidence a claim needs before it can justify a patch:
    0.90 for ``CRITICAL``, 0.80 for ``HIGH`` and 0.60 for ``LOW`` and ``MEDIUM``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HypothesisStatus(str, Enum):
    """Lifecycle state of a candidate explanation of the failure.

    A hypothesis starts ``ACTIVE`` or ``SUPPORTED``. Build feedback can mark it
    ``SUPPORTED`` or ``FALSIFIED``, and the one confirmed by a successful build
    becomes ``SELECTED``. ``WEAK`` is reserved for hypotheses with little support
    and is not set by the current code.
    """

    ACTIVE = "active"
    SUPPORTED = "supported"
    WEAK = "weak"
    FALSIFIED = "falsified"
    SELECTED = "selected"


class FailureFamily(str, Enum):
    """Broad class of the root build failure, as chosen by the forensics stage.

    Each member names the part of the build that failed: preparing the build
    root, resolving dependencies, configuring, compiling, linking, packaging,
    running tests, validating artifacts, or the build environment itself.
    ``UNKNOWN`` means no family signal matched.
    """

    BUILD_PREPARATION = "build_preparation"
    DEPENDENCY = "dependency"
    CONFIGURATION = "configuration"
    COMPILATION = "compilation"
    LINKING = "linking"
    PACKAGING = "packaging"
    TEST = "test"
    ARTIFACT = "artifact"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"


class FrontierStage(IntEnum):
    """How far a build got before it failed, as an ordered integer.

    A higher value means the build got further, so comparing two stages tells
    whether a repair made progress or caused a regression. ``SUCCESS`` means the
    build finished, and ``UNKNOWN`` (-1) means the stage could not be determined.
    """

    UNKNOWN = -1
    ENVIRONMENT = 0
    DEPENDENCY = 1
    CONFIGURE = 2
    COMPILE = 3
    LINK = 4
    INSTALL = 5
    PACKAGE = 6
    TEST = 7
    ARTIFACT_VALIDATION = 8
    SUCCESS = 9


class RepairFamily(str, Enum):
    """The kind of fix a hypothesis or candidate represents.

    Families group repairs for ranking and for exclusion: when build feedback
    shows a family made things worse, the controller stops proposing it.
    ``UNKNOWN`` means the family was not determined, and ``NO_SAFE_FIX`` records
    a deliberate decision to leave the package unchanged.
    """

    CONDITIONAL_COMPILER_FLAG = "conditional_compiler_flag"
    PORTABLE_FALLBACK = "portable_fallback"
    ARCHITECTURE_GUARD = "architecture_guard"
    DEPENDENCY_DECLARATION = "dependency_declaration"
    ARCHITECTURE_DEPENDENCY_CONDITION = "architecture_dependency_condition"
    TOOLCHAIN_FEATURE_DETECTION = "toolchain_feature_detection"
    ABI_TYPE_FIX = "abi_type_fix"
    ATOMIC_LINKAGE = "atomic_linkage"
    PACKAGING_RULE = "packaging_rule"
    ARTIFACT_PATH = "artifact_path"
    TEST_PORTABILITY = "test_portability"
    CONFIG_GENERATION = "config_generation"
    MULTI_FILE_SYNCHRONIZATION = "multi_file_synchronization"
    SOURCE_PORT = "source_port"
    UNKNOWN = "unknown"
    NO_SAFE_FIX = "no_safe_fix"


class RepairDecision(str, Enum):
    """What the controller did with an applied edit after judging its effect.

    ``KEEP`` leaves the edit in place (always the case when no build feedback is
    available), ``ROLLBACK`` undoes it after a regression or no change,
    ``SUCCESS`` means build feedback reported a passing build, and
    ``INCONCLUSIVE`` is reserved for feedback that settles nothing.
    """

    KEEP = "keep"
    ROLLBACK = "rollback"
    SUCCESS = "success"
    INCONCLUSIVE = "inconclusive"


@dataclass(slots=True)
class ArchitectureIdentity:
    """An architecture name as given by the Case, plus its canonical form.

    Attributes:
        canonical: The resolved architecture, or ``Architecture.UNKNOWN``.
        raw: The name exactly as it appeared in the Case metadata.
        aliases_observed: Other spellings of the same architecture seen while
            resolving it.
    """

    canonical: Architecture
    raw: str
    aliases_observed: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceItem:
    """One observed fact recorded in the evidence ledger.

    Attributes:
        id: Stable per-run identifier such as ``evidence-007``.
        kind: Where the fact came from.
        summary: Short plain-text statement of the fact.
        raw_excerpt: The supporting text, for example a log line; the ledger
            keeps at most 10,000 characters.
        source: Free-text description of the origin, such as a tool or file.
        path: Repository-relative file the fact refers to, if any.
        line_start: First line of the excerpt in ``path``, if known.
        line_end: Last line of the excerpt in ``path``, if known.
        reliability: How trustworthy this kind of source is, from 0 to 0.99.
        strength: How strongly the fact bears on the claims it supports, from
            0 to 1.
        created_during_stage: Controller stage that recorded the item.
    """

    id: str
    kind: EvidenceKind
    summary: str
    raw_excerpt: str = ""
    source: str = ""
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    reliability: float = 0.5
    strength: float = 1.0
    created_during_stage: str = ""


@dataclass(slots=True)
class Claim:
    """A statement the Agent believes, backed by evidence items.

    Attributes:
        id: Stable per-run identifier such as ``claim-003``.
        statement: The belief in plain words.
        category: Short label grouping related claims, such as flag provenance.
        confidence: Fused confidence from 0.01 to 0.99.
        confidence_band: Coarse label for ``confidence``.
        supporting_evidence_ids: Ledger IDs of evidence that supports the claim.
        contradicting_evidence_ids: Ledger IDs of evidence against the claim.
        consequence_level: How much damage acting on a wrong claim could do;
            it sets the confidence needed to justify a patch.
        status: Result of fusing the supporting and contradicting evidence.
    """

    id: str
    statement: str
    category: str
    confidence: float = 0.5
    confidence_band: ConfidenceBand = ConfidenceBand.MODERATE
    supporting_evidence_ids: list[str] = field(default_factory=list)
    contradicting_evidence_ids: list[str] = field(default_factory=list)
    consequence_level: ConsequenceLevel = ConsequenceLevel.MEDIUM
    status: ClaimStatus = ClaimStatus.OPEN


@dataclass(slots=True)
class Hypothesis:
    """A candidate explanation of the failure and the repair it suggests.

    Attributes:
        id: Stable per-run identifier such as ``hypothesis-001``.
        root_cause: The suspected cause in plain words.
        failure_family: Failure family the cause belongs to.
        prior_score: Belief in the hypothesis before any feedback, from 0 to 1.
        posterior_score: Belief after the evidence seen so far, from 0 to 1.
        supporting_claim_ids: Claims that support the hypothesis.
        contradicting_claim_ids: Claims that argue against it.
        repair_family: The kind of fix this cause calls for.
        falsification_conditions: Observations that would prove it wrong.
        candidate_probe_ids: Probes that could tell it apart from alternatives.
        status: Current lifecycle state.
    """

    id: str
    root_cause: str
    failure_family: FailureFamily
    prior_score: float
    posterior_score: float
    supporting_claim_ids: list[str] = field(default_factory=list)
    contradicting_claim_ids: list[str] = field(default_factory=list)
    repair_family: RepairFamily = RepairFamily.UNKNOWN
    falsification_conditions: list[str] = field(default_factory=list)
    candidate_probe_ids: list[str] = field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.ACTIVE


@dataclass(slots=True)
class ArchitectureFact:
    """One known property of the target architecture, such as its word size.

    Attributes:
        key: Name of the property, for example ``word_size`` or ``abi``.
        value: The property's value, or ``None`` when it is unknown.
        source: Where the value came from, such as ``build log`` or ``unknown``.
        confidence: Confidence in the value, from 0 to 1.
        evidence_ids: Ledger IDs of evidence behind the value.
    """

    key: str
    value: str | int | bool | None
    source: str
    confidence: float
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SemanticAnchor:
    """A conservative record of what an architecture-specific construct is for.

    Repairs that remove or guard such a construct are allowed only when its
    anchor shows the construct is an optimisation and not behaviour the
    package depends on.

    Attributes:
        site: Where the construct is, usually ``path:line``.
        symbol: The construct itself, such as a flag, header or macro name.
        implementation_description: What the construct does mechanically.
        functional_purpose: Why the package uses it.
        inputs: Values or data the construct reads.
        outputs: Values or data the construct produces.
        side_effects: Effects beyond its outputs.
        semantic_invariants: Behaviour any replacement must preserve.
        performance_intent: Whether and how the construct exists for speed.
        architecture_specific_mechanism: The part that only works on one
            architecture.
        portable_alternatives: Known portable ways to get the same behaviour.
        confidence: Confidence in the record, from 0 to 1.
        evidence_ids: Ledger IDs of evidence behind the record.
    """

    site: str
    symbol: str
    implementation_description: str
    functional_purpose: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    semantic_invariants: list[str] = field(default_factory=list)
    performance_intent: str = ""
    architecture_specific_mechanism: str = ""
    portable_alternatives: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FailureFrontier:
    """The point where a build stopped, used to compare attempts.

    Attributes:
        stage: How far the build got.
        error_signature: Normalised form of the primary diagnostic, with paths,
            numbers and temporary names replaced, so equal failures compare equal.
        primary_file: File named by the primary diagnostic, if any.
        primary_symbol: Symbol named by the primary diagnostic, if any.
        log_excerpt: Log text around the primary diagnostic.
        confidence: Confidence in the stage assignment, from 0 to 1.
    """

    stage: FrontierStage
    error_signature: str
    primary_file: str | None = None
    primary_symbol: str | None = None
    log_excerpt: str = ""
    confidence: float = 0.0


@dataclass(slots=True)
class FailureAnalysis:
    """Everything the forensics stage learned from one build log.

    Attributes:
        normalized_log: The log after normalisation (colour codes, timestamps
            and repeated lines removed).
        primary_diagnostic: The line judged to have caused the failure.
        family: The main failure family.
        secondary_families: Other families whose signals also matched.
        frontier: Where the build stopped.
        evidence_ids: Ledger IDs of evidence recorded for this analysis.
        rationale: Short explanation of why this diagnostic and family won.
    """

    normalized_log: str
    primary_diagnostic: str
    family: FailureFamily
    secondary_families: list[FailureFamily]
    frontier: FailureFrontier
    evidence_ids: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass(slots=True)
class Probe:
    """A cheap, side-effect-free check that can tell hypotheses apart.

    Attributes:
        id: Stable per-run identifier such as ``probe-001``.
        probe_type: What kind of check it is, for example ``repository_search``.
        description: What the probe looks for, in plain words.
        command: The internal operation and its argument; nothing is run as a
            shell command.
        working_directory: Directory the probe runs in, relative to the worktree.
        hypotheses_supported: Hypotheses that a positive result supports.
        hypotheses_refuted: Hypotheses that a positive result argues against.
        estimated_information_gain: Expected value of the result, from 0 to 1.
        estimated_cost: Relative cost of running the probe.
        side_effect_risk: Chance the probe changes anything, from 0 to 1.
        timeout_seconds: Time limit for the probe.
        requires_target_toolchain: Whether it needs a target-architecture
            compiler or tool.
        allowed: Whether policy permits running it.
    """

    id: str
    probe_type: str
    description: str
    command: list[str] = field(default_factory=list)
    working_directory: str = "."
    hypotheses_supported: list[str] = field(default_factory=list)
    hypotheses_refuted: list[str] = field(default_factory=list)
    estimated_information_gain: float = 0.0
    estimated_cost: float = 1.0
    side_effect_risk: float = 0.0
    timeout_seconds: float = 10.0
    requires_target_toolchain: bool = False
    allowed: bool = True


@dataclass(slots=True)
class PlannedEdit:
    """One exact-text change to a single repository file.

    Attributes:
        operation: One of ``replace_lines``, ``insert_before``, ``insert_after``
            or ``create_file``.
        path: Repository-relative path of the file to change.
        replacement: New text; for inserts, the text added next to
            ``expected_text``; for ``create_file``, the whole file.
        expected_text: Text that must occur exactly once in the file before the
            edit. Required for every operation except ``create_file``.
        expected_hash: Optional SHA-256 of the file's current bytes; the edit is
            refused if the file has changed since.
        line: Optional line hint. The executor ignores it and locates the edit
            by ``expected_text``.
        rationale: Why this particular change is needed.
        claim_dependencies: IDs of the claims the edit relies on.
    """

    operation: str
    path: str
    replacement: str
    expected_text: str | None = None
    expected_hash: str | None = None
    line: int | None = None
    rationale: str = ""
    claim_dependencies: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PatchCandidate:
    """A proposed repair: a set of edits with its justification and scores.

    Attributes:
        id: Stable per-run identifier of the candidate.
        repair_family: The kind of fix it represents.
        rationale: Why the edits should fix the failure.
        assumption_claim_ids: Claims that must hold for the repair to be right.
        planned_edits: The edits to apply, at most one per file.
        confidence: Confidence that the repair is correct, from 0 to 1.
        risk: Estimated risk of changing package behaviour, from 0 to 1;
            subtracted from the ranking score.
        expected_frontier: The stage the build should reach after the repair.
        expected_information_gain_if_built: How much a build of this candidate
            would teach, from 0 to 1; a build is requested only above 0.20.
        critic_findings: Problems reported by the portability critic.
        policy_findings: Problems reported by the policy critic.
        score_components: Named ranking inputs, each from 0 to 1.
    """

    id: str
    repair_family: RepairFamily
    rationale: str
    assumption_claim_ids: list[str]
    planned_edits: list[PlannedEdit]
    confidence: float
    risk: float
    expected_frontier: FrontierStage
    expected_information_gain_if_built: float
    critic_findings: list[str] = field(default_factory=list)
    policy_findings: list[str] = field(default_factory=list)
    score_components: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class RepairAttempt:
    """The record of one applied candidate and what happened to it.

    Attributes:
        attempt_number: 1-based position of the attempt in the run.
        selected_candidate_id: ID of the candidate that was applied.
        before_frontier: Where the build stopped before the edit.
        after_frontier: Where it stopped after the edit, or ``None`` when no
            build feedback was received.
        before_error_signature: Error signature before the edit.
        after_error_signature: Error signature after the edit, if known.
        modified_paths: Repository-relative paths the edit changed.
        build_requested: Whether a build was requested for this attempt.
        build_result: Frontier movement reported by the build, ``unavailable``
            if the request failed, or ``None`` if no build was requested.
        token_usage_if_known: Model tokens spent on the attempt, if known.
        elapsed_seconds: Wall-clock time the attempt took.
        decision: What the controller did with the edit afterwards.
    """

    attempt_number: int
    selected_candidate_id: str
    before_frontier: FailureFrontier
    after_frontier: FailureFrontier | None
    before_error_signature: str
    after_error_signature: str | None
    modified_paths: list[str]
    build_requested: bool
    build_result: str | None
    token_usage_if_known: int | None
    elapsed_seconds: float
    decision: RepairDecision


@dataclass(slots=True)
class CaseData:
    """The Case as loaded from ``/workspace/input``.

    Attributes:
        case_id: The Case identifier from its metadata.
        source_architecture: Architecture the package is known to build on.
        target_architecture: Architecture where the build fails.
        build_log: Text of the failing target-architecture build log, possibly
            cut to its head and tail when very long.
        metadata: The parsed ``case.json`` contents.
    """

    case_id: str
    source_architecture: ArchitectureIdentity
    target_architecture: ArchitectureIdentity
    build_log: str
    metadata: dict[str, JSONValue] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResult:
    """The document written to ``output/agent-result.json``.

    Attributes:
        schema_version: Protocol version of the document, currently ``0.1``.
        status: ``completed`` when the run finished, ``failed`` otherwise.
        message: Plain-text explanation of the outcome.
        modified_paths: Repository-relative paths the Agent changed.
    """

    schema_version: str
    status: str
    message: str
    modified_paths: list[str]

    def to_dict(self) -> dict[str, JSONValue]:
        """Convert the result into a plain dictionary ready for JSON encoding.

        Returns:
            A dictionary with the four fields in declaration order.
        """
        return asdict(self)
