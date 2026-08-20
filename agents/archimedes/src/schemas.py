"""Strongly typed data contracts shared across Archimedes subsystems."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import TypeAlias

JSONValue: TypeAlias = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)


class Architecture(str, Enum):
    X86_64 = "x86_64"
    AARCH64 = "aarch64"
    RISCV64 = "riscv64"
    UNKNOWN = "unknown"


class EvidenceKind(str, Enum):
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
    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ClaimStatus(str, Enum):
    OPEN = "open"
    SUPPORTED = "supported"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    RETIRED = "retired"


class ConsequenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HypothesisStatus(str, Enum):
    ACTIVE = "active"
    SUPPORTED = "supported"
    WEAK = "weak"
    FALSIFIED = "falsified"
    SELECTED = "selected"


class FailureFamily(str, Enum):
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
    KEEP = "keep"
    ROLLBACK = "rollback"
    SUCCESS = "success"
    INCONCLUSIVE = "inconclusive"


@dataclass(slots=True)
class ArchitectureIdentity:
    canonical: Architecture
    raw: str
    aliases_observed: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceItem:
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
    key: str
    value: str | int | bool | None
    source: str
    confidence: float
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SemanticAnchor:
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
    stage: FrontierStage
    error_signature: str
    primary_file: str | None = None
    primary_symbol: str | None = None
    log_excerpt: str = ""
    confidence: float = 0.0


@dataclass(slots=True)
class FailureAnalysis:
    normalized_log: str
    primary_diagnostic: str
    family: FailureFamily
    secondary_families: list[FailureFamily]
    frontier: FailureFrontier
    evidence_ids: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass(slots=True)
class Probe:
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
    case_id: str
    source_architecture: ArchitectureIdentity
    target_architecture: ArchitectureIdentity
    build_log: str
    metadata: dict[str, JSONValue] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResult:
    schema_version: str
    status: str
    message: str
    modified_paths: list[str]

    def to_dict(self) -> dict[str, JSONValue]:
        """Return a deterministic JSON-compatible protocol payload."""
        return asdict(self)
