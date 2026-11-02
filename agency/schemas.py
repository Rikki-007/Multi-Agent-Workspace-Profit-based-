"""Typed data contracts passed between agents in the job graph.

Every agent reads from and writes to a ``JobState``. The nested records
(``LeadRecord``, ``ProductSpec``, ``BuildResult``, ``QAReport``) are the only
shapes agents may exchange, so a malformed LLM output fails validation at the
node boundary instead of propagating downstream.
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    """Base for all contracts: unknown fields are rejected, assignments re-validated."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=False)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class EntityType(str, Enum):
    COMPANY = "company"
    SOLE_TRADER = "sole_trader"
    UNKNOWN = "unknown"


class LeadStatus(str, Enum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONTACTED = "contacted"
    WON = "won"
    LOST = "lost"


class LayoutType(str, Enum):
    SINGLE_PAGE = "single_page"
    MULTI_PAGE = "multi_page"
    LANDING_FUNNEL = "landing_funnel"


class ColorPalette(str, Enum):
    TRUST_BLUE = "trust_blue"
    ECO_GREEN = "eco_green"
    WARM_NEUTRAL = "warm_neutral"
    BOLD_DARK = "bold_dark"
    CLEAN_MONOCHROME = "clean_monochrome"
    VIBRANT_ACCENT = "vibrant_accent"


class TemplateTier(str, Enum):
    """Build tiers from src/templates/registry.json; each caps what a site may depend on."""

    T1 = "T1"  # Editorial: static, CSS-only motion
    T2 = "T2"  # Scroll Story: + Lenis, GSAP ScrollTrigger, Framer Motion
    T3 = "T3"  # Flagship 3D: + Three.js


class SiteStyle(str, Enum):
    MINIMALISM = "minimalism"
    GLASSMORPHISM = "glassmorphism"
    NEO_BRUTALISM = "neo_brutalism"
    LIQUID_GLASS = "liquid_glass"


class SectionType(str, Enum):
    HERO = "hero"
    SERVICES = "services"
    ABOUT = "about"
    TESTIMONIALS = "testimonials"
    GALLERY = "gallery"
    PRICING = "pricing"
    FAQ = "faq"
    SERVICE_AREA_MAP = "service_area_map"
    CONTACT = "contact"
    FOOTER = "footer"


class AcceptanceCriterion(str, Enum):
    BUILD_PASSES = "build_passes"
    LINT_PASSES = "lint_passes"
    NO_CONSOLE_ERRORS = "no_console_errors"
    LIGHTHOUSE_PERF_90 = "lighthouse_perf_90"
    MOBILE_RESPONSIVE = "mobile_responsive"
    ACCESSIBILITY_WCAG_AA = "accessibility_wcag_aa"
    SEO_META_PRESENT = "seo_meta_present"
    CONTACT_FORM_WORKS = "contact_form_works"


class ComplianceDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ErrorCategory(str, Enum):
    BUILD = "build"
    LINT = "lint"
    CONSOLE = "console"
    LIGHTHOUSE = "lighthouse"


class JobStage(str, Enum):
    NEW = "new"
    LEAD_DISCOVERY = "lead_discovery"
    COMPLIANCE_CHECK = "compliance_check"
    AWAITING_APPROVAL = "awaiting_approval"
    STRATEGY = "strategy"
    BUILD = "build"
    QA = "qa"
    OPTIMIZING = "optimizing"
    PREVIEW_DEPLOYED = "preview_deployed"
    AWAITING_CLIENT = "awaiting_client"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


class LeadRecord(StrictModel):
    business_name: str = Field(min_length=1, max_length=200)
    address: str | None = None
    entity_type: EntityType = EntityType.UNKNOWN
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    website: str | None = None
    source: str | None = Field(default=None, description="Where the lead was discovered.")
    status: LeadStatus = LeadStatus.DISCOVERED


class ProductSpec(StrictModel):
    layout: LayoutType
    color_palette: ColorPalette
    tier: TemplateTier = TemplateTier.T1
    style: SiteStyle = SiteStyle.MINIMALISM
    sections: list[SectionType] = Field(min_length=1)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    headline: str | None = Field(default=None, max_length=120)
    notes: str | None = None

    @field_validator("sections", "acceptance_criteria")
    @classmethod
    def _no_duplicates(cls, v: list[Enum]) -> list[Enum]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate entries are not allowed")
        return v

    @field_validator("sections")
    @classmethod
    def _required_sections(cls, v: list[SectionType]) -> list[SectionType]:
        missing = {SectionType.HERO, SectionType.CONTACT} - set(v)
        if missing:
            raise ValueError(f"spec must include sections: {sorted(s.value for s in missing)}")
        return v


class BuildResult(StrictModel):
    success: bool
    commit_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$")
    error_logs: list[str] = Field(default_factory=list)
    summary: str | None = Field(default=None, max_length=500, description="What the build agent changed.")

    @model_validator(mode="after")
    def _commit_required_on_success(self) -> BuildResult:
        if self.success and not self.commit_hash:
            raise ValueError("a successful build must report a commit_hash")
        return self


class QAReport(StrictModel):
    build_pass: bool
    linter_pass: bool
    lighthouse_score: int = Field(ge=0, le=100)
    console_errors: list[str] = Field(default_factory=list)
    lint_errors: list[str] = Field(default_factory=list)

    def passes(self, min_lighthouse: int = 90) -> bool:
        return (
            self.build_pass
            and self.linter_pass
            and self.lighthouse_score >= min_lighthouse
            and not self.console_errors
        )


class ObservedError(StrictModel):
    """One concrete failure, fingerprinted so it can be matched against past fixes."""

    category: ErrorCategory
    message: str = Field(max_length=4000)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{16}$", description="Exact match key (normalized text).")
    signature: str = Field(max_length=200, description="Coarse match key, e.g. 'build:TS2322'.")


class PendingFix(StrictModel):
    """What the optimizer asked the next build to fix; checked by QA to verify the patch."""

    base_commit: str | None = None
    errors: list[ObservedError] = Field(default_factory=list)


class Brief(StrictModel):
    """What the client configured (e.g. brief code MZ-T2-GLASS-7F3K), carried from intake to spec."""

    code: str = Field(pattern=r"^MZ-T[123]-(MINIMAL|GLASS|BRUTAL|LIQUID)-[A-HJ-NP-Z2-9]{4}$")
    tier: TemplateTier
    style: SiteStyle
    intake_id: str | None = None


class AuditEvent(StrictModel):
    at: datetime = Field(default_factory=_utcnow)
    node: str
    message: str
    level: Literal["info", "warn", "error"] = "info"
    data: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Graph state envelope
# --------------------------------------------------------------------------- #


class JobState(StrictModel):
    """Envelope carried through the graph. ``job_id`` doubles as the checkpoint thread_id."""

    job_id: str = Field(min_length=1)
    stage: JobStage = JobStage.NEW

    lead: LeadRecord | None = None
    brief: Brief | None = None  # set when the job came from a client intake
    compliance_flags: list[str] = Field(default_factory=list)
    compliance_decision: ComplianceDecision = ComplianceDecision.PENDING
    reviewer_notes: str | None = None

    spec: ProductSpec | None = None
    build_result: BuildResult | None = None
    qa_report: QAReport | None = None
    build_attempts: int = Field(default=0, ge=0)
    max_build_attempts: int = Field(default=3, ge=1)
    optimizer_feedback: list[str] = Field(default_factory=list)
    pending_fix: PendingFix | None = None
    lessons_context: str | None = Field(default=None, description="Past fixes injected into the next build.")
    injected_lesson_ids: list[int] = Field(default_factory=list)
    lessons_recorded: int = Field(default=0, ge=0)

    preview_url: str | None = None
    client_response: str | None = None

    # Append-only: each node returns new events and LangGraph concatenates them.
    audit_log: Annotated[list[AuditEvent], operator.add] = Field(default_factory=list)
