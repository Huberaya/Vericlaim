"""Persistent domain model for the VeriClaim multi-tenant SaaS foundation.

This module deliberately contains persistence concerns only: stable identifiers,
tenant ownership, immutable/versioned artefacts and relational integrity.  It
does not implement authentication, authorization decisions, document storage or
AI orchestration; those are delivered in their dedicated workstreams.

Every customer-owned aggregate carries ``organization_id``. PostgreSQL row
level security and request-scoped organization enforcement are activated by the
identity workstream. Application services must still never query a tenant-scoped
model without the validated organization context supplied by that workstream.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# The regulatory taxonomy owned by the engine. Verdict rows must record exactly
# the vocabulary the evaluator produced, not a re-encoded copy of it, otherwise a
# persisted verdict could drift from the rule that generated it.
from app.models.legal_types import (
    ClaimType as EngineClaimType,
    LegalForce as EngineLegalForce,
    OverallCompliance as EngineOverallCompliance,
    Severity as EngineSeverity,
    Verdict as EngineVerdict,
)

from app.core.database import Base

# ---------------------------------------------------------------------------
# Enumerations are persisted as their lower-case values, never as display text.
# ---------------------------------------------------------------------------


class OrganizationStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class UserStatus(str, Enum):
    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"


class MembershipStatus(str, Enum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class DocumentType(str, Enum):
    SUPPLIER_DECLARATION = "supplier_declaration"
    PRODUCT_SHEET = "product_sheet"
    MARKETING_ASSET = "marketing_asset"
    CERTIFICATE = "certificate"
    LCA_REPORT = "lca_report"
    ENVIRONMENTAL_DECLARATION = "environmental_declaration"
    LAB_REPORT = "lab_report"
    OTHER = "other"


class DocumentStatus(str, Enum):
    QUARANTINED = "quarantined"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ExtractionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REVIEW_REQUIRED = "review_required"


class DocumentExtractionJobStatus(str, Enum):
    """Lifecycle of the durable worker job, distinct from user-facing extraction state."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentUploadStatus(str, Enum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADED = "uploaded"
    SCANNING = "scanning"
    CLEAN = "clean"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"


class SegmentType(str, Enum):
    TITLE = "title"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    TABLE_CELL = "table_cell"
    FOOTNOTE = "footnote"
    IMAGE_OCR = "image_ocr"
    OTHER = "other"


class CertificateStatus(str, Enum):
    DECLARED = "declared"
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    EXPIRED = "expired"
    REJECTED = "rejected"
    OUT_OF_SCOPE = "out_of_scope"


class EvidenceType(str, Enum):
    CERTIFICATE = "certificate"
    LCA_REPORT = "lca_report"
    ENVIRONMENTAL_DECLARATION = "environmental_declaration"
    LAB_REPORT = "lab_report"
    RECYCLING_ROUTE = "recycling_route"
    GHG_INVENTORY = "ghg_inventory"
    GHG_REDUCTION_PLAN = "ghg_reduction_plan"
    CARBON_OFFSET = "carbon_offset"
    STANDARD = "standard"
    OTHER = "other"


class EvidenceStatus(str, Enum):
    PENDING = "pending"
    PRESENT = "present"
    PARTIAL = "partial"
    MISSING = "missing"
    EXPIRED = "expired"
    OUT_OF_SCOPE = "out_of_scope"
    VERIFIED = "verified"
    REJECTED = "rejected"


class AnalysisStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    EXTRACTING = "extracting"
    DETECTING_CLAIMS = "detecting_claims"
    CHECKING_EVIDENCE = "checking_evidence"
    SCORING = "scoring"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisDetectionJobStatus(str, Enum):
    """Lifecycle of the durable deterministic-claim detection job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    REVIEW_REQUIRED = "review_required"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class ClaimStatus(str, Enum):
    DETECTED = "detected"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    REVIEW_REQUIRED = "review_required"


class ClaimSource(str, Enum):
    DETERMINISTIC = "deterministic"
    AI_CANDIDATE = "ai_candidate"
    HUMAN = "human"


class EvidenceRelation(str, Enum):
    SUPPORTS = "supports"
    PARTIALLY_SUPPORTS = "partially_supports"
    CONTRADICTS = "contradicts"
    NOT_RELATED = "not_related"
    REVIEW_REQUIRED = "review_required"


class RegulationStatus(str, Enum):
    DRAFT = "draft"
    IN_FORCE = "in_force"
    UPCOMING = "upcoming"
    SUPERSEDED = "superseded"
    RETIRED = "retired"
    PROPOSAL = "proposal"
    VOLUNTARY_STANDARD = "voluntary_standard"


class RuleStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class LegalForce(str, Enum):
    BINDING_NATIONAL = "binding_national"
    EU_DIRECTIVE = "eu_directive"
    EU_REGULATION = "eu_regulation"
    PROPOSAL_ONLY = "proposal_only"
    VOLUNTARY_STANDARD = "voluntary_standard"
    INTERNAL_POLICY = "internal_policy"


class RecommendationStatus(str, Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    IMPLEMENTED = "implemented"


class ValidationDecision(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    CONTESTED = "contested"
    OVERRIDDEN = "overridden"


class ReportStatus(str, Enum):
    REQUESTED = "requested"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class ReportJobStatus(str, Enum):
    """Cycle de vie du travail de génération, distinct de l'état du rapport.

    ``ReportStatus`` décrit ce qu'un client peut faire du document (il existe, il
    est en cours, il a échoué) ; ``ReportJobStatus`` décrit la tentative en cours.
    Les confondre rendrait impossible la reprise d'un travail interrompu : un
    rapport « en cours » ne dit pas s'il faut réessayer.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SupportRequestCategory(str, Enum):
    """Ce sur quoi porte une demande de support.

    La catégorie n'est pas décorative : elle décide du délai de première réponse
    appliqué (une indisponibilité de service n'attend pas comme une question de
    facturation) et elle est publiée dans le centre d'aide.
    """

    QUESTION = "question"          # fonctionnement du produit
    INCIDENT = "incident"          # quelque chose ne fonctionne pas
    EXTRACTION = "extraction"      # documents, OCR, segments, analyse
    REPORT = "report"              # rapport signé, téléchargement, vérification
    BILLING = "billing"            # plan, quota, facture
    PRIVACY = "privacy"            # droits des personnes (renvoi vers C12)
    OTHER = "other"


class SupportRequestStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"


class DataSubjectRight(str, Enum):
    """Les droits qu'une personne peut exercer, nommés comme le règlement.

    Chaque valeur correspond à un article du RGPD, parce que la procédure publiée
    doit renvoyer au texte, pas à une nomenclature interne.
    """

    ACCESS = "access"                    # art. 15
    RECTIFICATION = "rectification"      # art. 16
    ERASURE = "erasure"                  # art. 17
    RESTRICTION = "restriction"          # art. 18
    PORTABILITY = "portability"          # art. 20
    OBJECTION = "objection"              # art. 21


class DataSubjectRequestStatus(str, Enum):
    RECEIVED = "received"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REFUSED = "refused"
    WITHDRAWN = "withdrawn"


class DataSubjectRequestOutcome(str, Enum):
    GRANTED = "granted"
    PARTIALLY_GRANTED = "partially_granted"
    REFUSED = "refused"


class EvidenceRequestStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


def _enum(enum_class: type[Enum], name: str) -> SAEnum:
    """Build portable VARCHAR-backed enums for PostgreSQL and SQLite.

    Native PostgreSQL enums make regulatory taxonomy changes operationally
    expensive.  Constraint-backed VARCHAR values keep migrations explicit and
    remain testable on SQLite.
    """

    return SAEnum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Shared mapping mixins.
# ---------------------------------------------------------------------------


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class TenantScopedMixin:
    """Marker and foreign key shared by every customer-owned aggregate."""

    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


# ---------------------------------------------------------------------------
# Identity and tenancy. Authentication is intentionally not implemented here.
# ---------------------------------------------------------------------------


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    status: Mapped[OrganizationStatus] = mapped_column(
        _enum(OrganizationStatus, "organization_status"),
        nullable=False,
        default=OrganizationStatus.ACTIVE,
    )
    billing_plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_region: Mapped[str] = mapped_column(String(32), nullable=False, default="eu")

    memberships: Mapped[list[Membership]] = relationship(back_populates="organization")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        _enum(UserStatus, "user_status"), nullable=False, default=UserStatus.INVITED
    )
    identity_provider: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # C14 self-service accounts. ``password_hash`` is NULL for SSO-only users: an
    # account created by OIDC has no password until its owner sets one, and a
    # missing hash can never authenticate (verify_password refuses NULL).
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", foreign_keys="Membership.user_id"
    )
    auth_sessions: Mapped[list[AuthSession]] = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("identity_provider", "external_subject", name="uq_users_identity_subject"),
    )


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system_role: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    permissions_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    memberships: Mapped[list[Membership]] = relationship(back_populates="role")


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memberships"

    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        _enum(MembershipStatus, "membership_status"), nullable=False, default=MembershipStatus.INVITED
    )
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="memberships", foreign_keys=[organization_id])
    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])
    role: Mapped[Role] = relationship(back_populates="memberships")

    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_memberships_organization_user"),)


class IdentityTokenPurpose(str, Enum):
    """What a one-shot identity token is allowed to do.

    One table, one purpose per row: a token issued for an invitation can never be
    replayed as a password reset.
    """

    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"
    INVITATION = "invitation"


class IdentityToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single-use token backing verification, reset and invitation links.

    Only the SHA-256 digest is stored, exactly like sessions: a database dump does
    not hand out working links. ``organization_id`` is nullable because an e-mail
    verification happens before any organization is joined; when it is set, it
    scopes an invitation to the organization that issued it.
    """

    __tablename__ = "identity_tokens"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    purpose: Mapped[str] = mapped_column(
        _enum(IdentityTokenPurpose, "identity_token_purpose"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="ck_identity_tokens_hash_length"),
    )


class EmailMessageStatus(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


class EmailMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Outbox for transactional e-mails.

    Every message the product wants to send is written here first. Two reasons:
    an operator can see what was attempted (including on an instance where no
    provider is configured), and the automated tests assert on a real record
    rather than on a mock that would hide a broken journey.

    ``sent_at`` is only set when a transport really accepted the message. On an
    instance without a configured provider the row stays ``not_configured`` —
    the product never pretends an e-mail left the building.
    """

    __tablename__ = "email_messages"

    organization_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        _enum(EmailMessageStatus, "email_message_status"),
        nullable=False,
        default=EmailMessageStatus.QUEUED,
        index=True,
    )
    transport: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PasswordLoginAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Failure counter for password logins, keyed by normalized e-mail.

    Deliberately not keyed by IP: an attacker with a botnet would bypass an IP
    counter, while a shared office IP would lock out a whole team. The counter
    makes online guessing against one account expensive, which is the threat that
    matters for a password fallback. It is not a substitute for MFA.
    """

    __tablename__ = "password_login_attempts"

    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("failed_count >= 0", name="ck_password_login_attempts_count_positive"),
    )


class AuthSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Opaque server-side session created after a verified OIDC callback.

    Browser cookies only hold a random bearer token.  The database stores an
    HMAC digest, never the raw token or an upstream provider token.
    """

    __tablename__ = "auth_sessions"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    active_organization_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(back_populates="auth_sessions")

    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="ck_auth_sessions_token_hash_length"),
        CheckConstraint("length(csrf_token_hash) = 64", name="ck_auth_sessions_csrf_token_hash_length"),
        CheckConstraint(
            "user_agent_hash IS NULL OR length(user_agent_hash) = 64",
            name="ck_auth_sessions_user_agent_hash_length",
        ),
    )


# ---------------------------------------------------------------------------
# Supplier, product and document aggregates.
# ---------------------------------------------------------------------------


class Supplier(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "suppliers"

    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trading_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Creation retries are tenant-scoped. Legacy/foundation rows deliberately
    # retain NULL keys because they predate the public catalog API.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    products: Mapped[list[Product]] = relationship(back_populates="supplier")
    documents: Mapped[list[Document]] = relationship(back_populates="supplier")

    __table_args__ = (
        UniqueConstraint("organization_id", "external_reference", name="uq_suppliers_organization_external_reference"),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_suppliers_organization_idempotency_key",
        ),
        CheckConstraint("country_code IS NULL OR length(country_code) = 2", name="ck_suppliers_country_code_length"),
        CheckConstraint(
            "request_sha256 IS NULL OR length(request_sha256) = 64",
            name="ck_suppliers_request_sha256_length",
        ),
    )


class Product(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "products"

    supplier_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country_of_sale: Mapped[str | None] = mapped_column(String(2), nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # Mirrors Supplier idempotency so a transient client retry can never create
    # a second product reference in the same organisation.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    supplier: Mapped[Supplier] = relationship(back_populates="products")
    documents: Mapped[list[Document]] = relationship(back_populates="product")

    __table_args__ = (
        UniqueConstraint("organization_id", "reference", name="uq_products_organization_reference"),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_products_organization_idempotency_key",
        ),
        CheckConstraint("country_of_sale IS NULL OR length(country_of_sale) = 2", name="ck_products_country_of_sale_length"),
        CheckConstraint(
            "request_sha256 IS NULL OR length(request_sha256) = 64",
            name="ck_products_request_sha256_length",
        ),
    )


class Document(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, SoftDeleteMixin, Base):
    """Logical document. Immutable binary/text changes are stored as versions."""

    __tablename__ = "documents"

    supplier_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    document_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(
        _enum(DocumentType, "document_type"), nullable=False, default=DocumentType.OTHER
    )
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus, "document_status"), nullable=False, default=DocumentStatus.QUARANTINED
    )
    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    supplier: Mapped[Supplier | None] = relationship(back_populates="documents")
    product: Mapped[Product | None] = relationship(back_populates="documents")
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version_number"
    )

    __table_args__ = (UniqueConstraint("organization_id", "document_key", name="uq_documents_organization_key"),)


class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Immutable physical/document-extraction version stored outside the DB."""

    __tablename__ = "document_versions"

    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        _enum(ExtractionStatus, "extraction_status"), nullable=False, default=ExtractionStatus.PENDING
    )
    extraction_engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extracted_text_storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    extracted_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extraction_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[Document] = relationship(back_populates="versions")
    segments: Mapped[list[DocumentSegment]] = relationship(
        back_populates="document_version", cascade="all, delete-orphan", order_by="DocumentSegment.sequence_number"
    )
    extraction_job: Mapped[DocumentExtractionJob | None] = relationship(
        back_populates="document_version", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_versions_document_number"),
        CheckConstraint("version_number > 0", name="ck_document_versions_positive_number"),
        CheckConstraint("size_bytes >= 0", name="ck_document_versions_nonnegative_size"),
        CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_document_versions_positive_pages"),
        CheckConstraint("length(sha256) = 64", name="ck_document_versions_sha256_length"),
        CheckConstraint(
            "extracted_text_sha256 IS NULL OR length(extracted_text_sha256) = 64",
            name="ck_document_versions_extracted_sha256_length",
        ),
    )


class DocumentExtractionJob(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Durable, at-least-once extraction job for one immutable document version.

    A job is intentionally unique per version. Successful extraction is never
    silently overwritten; a future extraction-engine revision requires a new
    explicit versioning workflow rather than replaying a completed job.
    """

    __tablename__ = "document_extraction_jobs"

    document_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[DocumentExtractionJobStatus] = mapped_column(
        _enum(DocumentExtractionJobStatus, "document_extraction_job_status"),
        nullable=False,
        default=DocumentExtractionJobStatus.QUEUED,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)

    document_version: Mapped[DocumentVersion] = relationship(back_populates="extraction_job")

    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_document_extraction_jobs_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_document_extraction_jobs_max_attempts_positive"),
        CheckConstraint("max_attempts >= attempt_count", name="ck_document_extraction_jobs_attempts_within_max"),
        Index(
            "ix_document_extraction_jobs_claim",
            "organization_id",
            "status",
            "available_at",
        ),
    )


class DocumentUpload(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Short-lived quarantine upload intent; never a clean document version itself."""

    __tablename__ = "document_uploads"

    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    finalized_document_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    quarantine_storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    declared_content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[DocumentUploadStatus] = mapped_column(
        _enum(DocumentUploadStatus, "document_upload_status"),
        nullable=False,
        default=DocumentUploadStatus.PENDING_UPLOAD,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    scan_engine: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scan_signature_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scan_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scan_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scan_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint("declared_size_bytes > 0", name="ck_document_uploads_declared_size_positive"),
        CheckConstraint(
            "uploaded_size_bytes IS NULL OR uploaded_size_bytes > 0",
            name="ck_document_uploads_uploaded_size_positive",
        ),
        CheckConstraint(
            "expected_sha256 IS NULL OR length(expected_sha256) = 64",
            name="ck_document_uploads_expected_sha256_length",
        ),
        CheckConstraint(
            "uploaded_sha256 IS NULL OR length(uploaded_sha256) = 64",
            name="ck_document_uploads_uploaded_sha256_length",
        ),
    )


class DocumentSegment(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """A citeable extracted segment with page/offset provenance."""

    __tablename__ = "document_segments"

    document_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    segment_type: Mapped[SegmentType] = mapped_column(
        _enum(SegmentType, "segment_type"), nullable=False, default=SegmentType.PARAGRAPH
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bounding_box_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    document_version: Mapped[DocumentVersion] = relationship(back_populates="segments")

    __table_args__ = (
        UniqueConstraint("document_version_id", "sequence_number", name="uq_document_segments_version_sequence"),
        CheckConstraint("sequence_number >= 0", name="ck_document_segments_nonnegative_sequence"),
        CheckConstraint("page_number IS NULL OR page_number > 0", name="ck_document_segments_positive_page"),
        CheckConstraint(
            "start_offset IS NULL OR end_offset IS NULL OR end_offset >= start_offset",
            name="ck_document_segments_valid_offsets",
        ),
        CheckConstraint("length(source_sha256) = 64", name="ck_document_segments_sha256_length"),
    )


# ---------------------------------------------------------------------------
# Evidence and certificates. These records link to immutable document versions.
# ---------------------------------------------------------------------------


class Certificate(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "certificates"

    document_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supplier_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scheme: Mapped[str] = mapped_column(String(128), nullable=False)
    license_number: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[CertificateStatus] = mapped_column(
        _enum(CertificateStatus, "certificate_status"), nullable=False, default=CertificateStatus.DECLARED
    )
    registry_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    registry_source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    registry_snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "scheme", "license_number", name="uq_certificates_organization_scheme_license"),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name="ck_certificates_valid_date_range",
        ),
        CheckConstraint(
            "registry_snapshot_sha256 IS NULL OR length(registry_snapshot_sha256) = 64",
            name="ck_certificates_registry_sha256_length",
        ),
    )


class Evidence(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "evidence"

    document_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    certificate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("certificates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supplier_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(
        _enum(EvidenceType, "evidence_type"), nullable=False
    )
    status: Mapped[EvidenceStatus] = mapped_column(
        _enum(EvidenceStatus, "evidence_status"), nullable=False, default=EvidenceStatus.PENDING
    )
    reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    product_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    document_version: Mapped[DocumentVersion | None] = relationship(foreign_keys=[document_version_id])
    certificate: Mapped[Certificate | None] = relationship(foreign_keys=[certificate_id])
    supplier: Mapped[Supplier | None] = relationship(foreign_keys=[supplier_id])
    product: Mapped[Product | None] = relationship(foreign_keys=[product_id])
    verified_by_user: Mapped[User | None] = relationship(foreign_keys=[verified_by_user_id])
    evidence_links: Mapped[list[EvidenceLink]] = relationship(back_populates="evidence", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "expires_on IS NULL OR issued_on IS NULL OR expires_on >= issued_on",
            name="ck_evidence_valid_date_range",
        ),
    )


# ---------------------------------------------------------------------------
# Analysis, claims, evidence links, risks and recommendations.
# ---------------------------------------------------------------------------


class Analysis(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, SoftDeleteMixin, Base):
    """Logical audit dossier; results are immutable ``AnalysisVersion`` rows."""

    __tablename__ = "analyses"

    supplier_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    analysis_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[AnalysisStatus] = mapped_column(
        _enum(AnalysisStatus, "analysis_status"), nullable=False, default=AnalysisStatus.DRAFT
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list[AnalysisVersion]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", order_by="AnalysisVersion.version_number"
    )
    documents: Mapped[list[AnalysisDocument]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("organization_id", "analysis_key", name="uq_analyses_organization_key"),)


class AnalysisDocument(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Input document version captured for a reproducible analysis run."""

    __tablename__ = "analysis_documents"

    analysis_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, default="source")
    attached_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    analysis: Mapped[Analysis] = relationship(back_populates="documents")

    __table_args__ = (
        UniqueConstraint("analysis_id", "document_version_id", "purpose", name="uq_analysis_documents_version_purpose"),
    )


class AnalysisVersion(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "analysis_versions"

    analysis_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AnalysisStatus] = mapped_column(
        _enum(AnalysisStatus, "analysis_version_status"), nullable=False, default=AnalysisStatus.DRAFT
    )
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rulebook_version: Mapped[str] = mapped_column(String(128), nullable=False)
    # Idempotency is scoped to the tenant so a replay cannot create a second
    # immutable version. Legacy foundation rows remain valid with NULL values.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Computed regulatory conclusion for this immutable version. NULL means the
    # version was produced before the verdict pipeline existed; it must never be
    # rendered as if a conclusion had been reached.
    # Stored as a constrained VARCHAR rather than a PostgreSQL enum type: the
    # value is added to an existing table, and a table-referencing CHECK can be
    # dropped explicitly, which SQLite's table-recreation batch mode requires.
    overall_compliance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Deliberately still NULL: the engine emits a 0-100 risk_score and an
    # overall_compliance verdict, but no RiskLevel taxonomy. Fabricating a
    # mapping would invent a legal signal the rule book does not define.
    overall_risk_level: Mapped[RiskLevel | None] = mapped_column(
        _enum(RiskLevel, "analysis_risk_level"), nullable=True
    )
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Frozen inputs of the evaluation: the audit context and the engine-side
    # switches (Rule Book fingerprint, transposition status) that produced the
    # verdicts. Persisting them is what makes a replayed analysis identical.
    evaluation_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    analysis: Mapped[Analysis] = relationship(back_populates="versions")
    detection_job: Mapped[AnalysisDetectionJob | None] = relationship(
        back_populates="analysis_version", uselist=False, cascade="all, delete-orphan"
    )
    claims: Mapped[list[Claim]] = relationship(back_populates="analysis_version", cascade="all, delete-orphan")
    verdicts: Mapped[list[AnalysisVerdict]] = relationship(
        back_populates="analysis_version", cascade="all, delete-orphan"
    )
    risks: Mapped[list[Risk]] = relationship(back_populates="analysis_version", cascade="all, delete-orphan")
    recommendations: Mapped[list[Recommendation]] = relationship(
        back_populates="analysis_version", cascade="all, delete-orphan"
    )
    validations: Mapped[list[Validation]] = relationship(
        back_populates="analysis_version", cascade="all, delete-orphan"
    )
    reports: Mapped[list[Report]] = relationship(back_populates="analysis_version", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("analysis_id", "version_number", name="uq_analysis_versions_analysis_number"),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_analysis_versions_organization_idempotency_key",
        ),
        CheckConstraint("version_number > 0", name="ck_analysis_versions_positive_number"),
        CheckConstraint(
            "overall_compliance IS NULL OR overall_compliance IN "
            "('COMPLIANT', 'NON_COMPLIANT', 'CONDITIONAL_REJECT', "
            "'REVIEW_REQUIRED', 'UPCOMING_REQUIREMENTS', 'NO_CLAIMS_DETECTED')",
            name="ck_analysis_versions_overall_compliance",
        ),
        CheckConstraint("risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)", name="ck_analysis_versions_risk_score"),
        CheckConstraint("length(input_manifest_sha256) = 64", name="ck_analysis_versions_input_sha256_length"),
        CheckConstraint(
            "request_sha256 IS NULL OR length(request_sha256) = 64",
            name="ck_analysis_versions_request_sha256_length",
        ),
        CheckConstraint(
            "result_sha256 IS NULL OR length(result_sha256) = 64",
            name="ck_analysis_versions_result_sha256_length",
        ),
    )


class OrganizationRetentionPolicy(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """A retention policy a customer has actually declared, or no row at all.

    Before this table the endpoint returned the same literals for every tenant:
    ``dpo@vericlaim.ai``, ``AES-256 / TLS 1.3``, ``EU (Paris / Frankfurt)``, 5 and
    10 years, reviewed on a fixed date. Those were presented as a compliance
    artefact and were false for every client, and an unverified hosting region
    displayed by the publisher is a contractual commitment nobody made.

    Therefore: no row means "not configured", and the API says so with ``null``
    values. Every field here is a declaration by the organization, stored with who
    declared it and when; nothing is inferred, and nothing is defaulted.
    """

    __tablename__ = "organization_retention_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_organization_retention_policies_org"),
        CheckConstraint(
            "documents_retention_years IS NULL OR documents_retention_years > 0",
            name="ck_org_retention_documents_positive",
        ),
        CheckConstraint(
            "audit_trail_retention_years IS NULL OR audit_trail_retention_years > 0",
            name="ck_org_retention_audit_positive",
        ),
        CheckConstraint(
            "evidence_archive_retention_years IS NULL OR evidence_archive_retention_years > 0",
            name="ck_org_retention_evidence_positive",
        ),
        CheckConstraint(
            "storage_region IS NULL OR length(storage_region) > 0",
            name="ck_org_retention_region_not_blank",
        ),
    )

    documents_retention_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audit_trail_retention_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_archive_retention_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Declared, never asserted by the platform: the platform does not know where
    # the customer's data actually sits unless an operator says so.
    gdpr_contact_email: Mapped[str | None] = mapped_column(String(length=320), nullable=True)
    encryption_standard: Mapped[str | None] = mapped_column(String(length=64), nullable=True)
    storage_region: Mapped[str | None] = mapped_column(String(length=128), nullable=True)
    export_formats_supported: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    last_policy_review: Mapped[date | None] = mapped_column(Date, nullable=True)
    configured_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisVerdict(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """One persisted regulatory verdict for one claim and one formal rule.

    ``AnalysisVersion.result_json`` stays a summary; this table is the record a
    report is rendered from. It stores the rule that was applied, the reasoning
    trace and the remediation exactly as the deterministic engine produced them,
    together with the fingerprints needed to replay the decision years later.

    A verdict is never recomputed at read time. If the Rule Book changes, a new
    ``AnalysisVersion`` is created; an existing row is immutable.
    """

    __tablename__ = "analysis_verdicts"

    analysis_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("claims.id", ondelete="SET NULL"), nullable=True, index=True
    )
    document_segment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_segments.id", ondelete="SET NULL"), nullable=True
    )
    # Stable ordering so a replayed analysis yields byte-identical output.
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Claim snapshot, so the verdict remains readable even if the claim row is
    # ever detached.
    claim_type: Mapped[str] = mapped_column(_enum(EngineClaimType, "verdict_claim_type"), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Applied rule.
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    rule_title: Mapped[str] = mapped_column(Text, nullable=False)
    law_reference: Mapped[str] = mapped_column(Text, nullable=False)
    legal_force: Mapped[str] = mapped_column(_enum(EngineLegalForce, "verdict_legal_force"), nullable=False)
    severity: Mapped[str] = mapped_column(_enum(EngineSeverity, "verdict_severity"), nullable=False)
    verdict: Mapped[str] = mapped_column(_enum(EngineVerdict, "verdict_value"), nullable=False, index=True)
    is_legal_violation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    safe_harbor_applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    safe_harbor_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_caveat: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_urls_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    required_evidence_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence_checks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    reasoning_steps_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    remediation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sanction_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Replay fingerprints: which engine and which rule book produced this row.
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rulebook_version: Mapped[str] = mapped_column(String(128), nullable=False)

    analysis_version: Mapped[AnalysisVersion] = relationship(back_populates="verdicts")

    __table_args__ = (
        UniqueConstraint(
            "analysis_version_id",
            "sequence_number",
            name="uq_analysis_verdicts_version_sequence",
        ),
        UniqueConstraint(
            "analysis_version_id",
            "claim_id",
            "rule_id",
            name="uq_analysis_verdicts_version_claim_rule",
        ),
        CheckConstraint("sequence_number >= 0", name="ck_analysis_verdicts_sequence"),
    )


class AnalysisDetectionJob(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Durable, at-least-once claim-detection job for one analysis version."""

    __tablename__ = "analysis_detection_jobs"

    analysis_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[AnalysisDetectionJobStatus] = mapped_column(
        _enum(AnalysisDetectionJobStatus, "analysis_detection_job_status"),
        nullable=False,
        default=AnalysisDetectionJobStatus.QUEUED,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)

    analysis_version: Mapped[AnalysisVersion] = relationship(back_populates="detection_job")

    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_analysis_detection_jobs_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_analysis_detection_jobs_max_attempts_positive"),
        CheckConstraint("max_attempts >= attempt_count", name="ck_analysis_detection_jobs_attempts_within_max"),
        Index(
            "ix_analysis_detection_jobs_claim",
            "organization_id",
            "status",
            "available_at",
        ),
    )


class Claim(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "claims"

    analysis_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_segment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_segments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    claim_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[ClaimSource] = mapped_column(
        _enum(ClaimSource, "claim_source"), nullable=False, default=ClaimSource.DETERMINISTIC
    )
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[ClaimStatus] = mapped_column(
        _enum(ClaimStatus, "claim_status"), nullable=False, default=ClaimStatus.DETECTED
    )
    detector_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    analysis_version: Mapped[AnalysisVersion] = relationship(back_populates="claims")
    document_segment: Mapped[DocumentSegment | None] = relationship(foreign_keys=[document_segment_id])
    evidence_links: Mapped[list[EvidenceLink]] = relationship(back_populates="claim", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "start_offset IS NULL OR end_offset IS NULL OR end_offset >= start_offset",
            name="ck_claims_valid_offsets",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_claims_confidence_score",
        ),
    )


class EvidenceLink(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Explicit, reviewable relation between a detected claim and evidence."""

    __tablename__ = "evidence_links"

    claim_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evidence.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    relation: Mapped[EvidenceRelation] = mapped_column(
        _enum(EvidenceRelation, "evidence_relation"), nullable=False, default=EvidenceRelation.REVIEW_REQUIRED
    )
    coverage_status: Mapped[EvidenceStatus] = mapped_column(
        _enum(EvidenceStatus, "evidence_link_coverage_status"), nullable=False, default=EvidenceStatus.PENDING
    )
    validity_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    claim: Mapped[Claim] = relationship(back_populates="evidence_links")
    evidence: Mapped[Evidence] = relationship(back_populates="evidence_links", foreign_keys=[evidence_id])
    reviewed_by_user: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_user_id])

    __table_args__ = (
        UniqueConstraint("claim_id", "evidence_id", name="uq_evidence_links_claim_evidence"),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_evidence_links_confidence_score",
        ),
    )


class Risk(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "risks"

    analysis_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("claims.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    risk_type: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[RiskLevel] = mapped_column(_enum(RiskLevel, "risk_level"), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    factors_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    source_citations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    analysis_version: Mapped[AnalysisVersion] = relationship(back_populates="risks")

    __table_args__ = (
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="ck_risks_score"),
    )


class Recommendation(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "recommendations"

    analysis_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("claims.id", ondelete="SET NULL"), nullable=True, index=True
    )
    recommendation_type: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[RiskLevel] = mapped_column(_enum(RiskLevel, "recommendation_priority"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    requires_human_validation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[RecommendationStatus] = mapped_column(
        _enum(RecommendationStatus, "recommendation_status"), nullable=False, default=RecommendationStatus.OPEN
    )

    analysis_version: Mapped[AnalysisVersion] = relationship(back_populates="recommendations")


# ---------------------------------------------------------------------------
# Regulation and rules. Global rules have ``organization_id`` set to NULL;
# tenant-specific internal policies are scoped to an organization.
# ---------------------------------------------------------------------------


class Regulation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "regulations"

    jurisdiction_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    authority: Mapped[str | None] = mapped_column(String(255), nullable=True)
    official_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[RegulationStatus] = mapped_column(
        _enum(RegulationStatus, "regulation_status"), nullable=False
    )
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_text_storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    rules: Mapped[list[Rule]] = relationship(back_populates="regulation")

    __table_args__ = (
        UniqueConstraint("jurisdiction_code", "identifier", name="uq_regulations_jurisdiction_identifier"),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until >= effective_from",
            name="ck_regulations_effective_date_range",
        ),
        CheckConstraint(
            "source_snapshot_sha256 IS NULL OR length(source_snapshot_sha256) = 64",
            name="ck_regulations_source_sha256_length",
        ),
    )


class Rule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rules"

    organization_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    regulation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("regulations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    claim_category: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sector_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    legal_force: Mapped[LegalForce] = mapped_column(_enum(LegalForce, "rule_legal_force"), nullable=False)
    default_risk_level: Mapped[RiskLevel] = mapped_column(
        _enum(RiskLevel, "rule_default_risk_level"), nullable=False
    )
    status: Mapped[RuleStatus] = mapped_column(_enum(RuleStatus, "rule_status"), nullable=False, default=RuleStatus.DRAFT)
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    regulation: Mapped[Regulation | None] = relationship(back_populates="rules")
    versions: Mapped[list[RuleVersion]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", order_by="RuleVersion.version_number"
    )

    __table_args__ = (CheckConstraint("current_version_number > 0", name="ck_rules_positive_current_version"),)


class RuleVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rule_versions"

    # NULL denotes a globally curated regulatory rule; a non-NULL value denotes
    # an organization-specific internal policy and must match Rule.organization_id.
    organization_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    rule_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    legal_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    decision_logic_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_rule_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rule_versions.id", ondelete="SET NULL"), nullable=True
    )

    rule: Mapped[Rule] = relationship(back_populates="versions", foreign_keys=[rule_id])

    __table_args__ = (
        UniqueConstraint("rule_id", "version_number", name="uq_rule_versions_rule_number"),
        CheckConstraint("version_number > 0", name="ck_rule_versions_positive_number"),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until >= effective_from",
            name="ck_rule_versions_effective_date_range",
        ),
    )


# ---------------------------------------------------------------------------
# Human review, reports, supplier evidence requests and append-only audit log.
# ---------------------------------------------------------------------------


class Validation(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "validations"

    analysis_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("claims.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision: Mapped[ValidationDecision] = mapped_column(
        _enum(ValidationDecision, "validation_decision"), nullable=False, default=ValidationDecision.PENDING
    )
    reviewer_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    analysis_version: Mapped[AnalysisVersion] = relationship(back_populates="validations")
    claim: Mapped[Claim | None] = relationship(foreign_keys=[claim_id])
    reviewer_user: Mapped[User | None] = relationship(foreign_keys=[reviewer_user_id])


class Report(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "reports"

    analysis_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    report_format: Mapped[str] = mapped_column(String(32), nullable=False, default="pdf")
    status: Mapped[ReportStatus] = mapped_column(
        _enum(ReportStatus, "report_status"), nullable=False, default=ReportStatus.REQUESTED
    )
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True, unique=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # C22 — le binaire est stocké par le worker et téléchargé plus tard : la taille
    # et le type de contenu sont des faits sur l'objet stocké, pas des suppositions
    # du lecteur.
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # --- C7: probative integrity -------------------------------------------------
    # ``verification_reference`` is the only thing a third party needs. It is a
    # random handle rather than the analysis UUID because the verification
    # endpoint is public: an internal identifier would leak that an organisation
    # exists and is running analyses.
    verification_reference: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature_key_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    signature_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Fingerprint of the analysis as it stood when this report was signed. A
    # later change to the version makes verification fail instead of silently
    # validating a stale document.
    signed_result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_rulebook_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signed_engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    analysis_version: Mapped[AnalysisVersion] = relationship(back_populates="reports")

    __table_args__ = (
        UniqueConstraint("analysis_version_id", "version_number", "report_format", name="uq_reports_analysis_version_format"),
        CheckConstraint("version_number > 0", name="ck_reports_positive_number"),
        CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="ck_reports_sha256_length"),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0", name="ck_reports_size_positive"
        ),
        CheckConstraint(
            "signature IS NULL OR length(signature) = 64", name="ck_reports_signature_length"
        ),
        CheckConstraint(
            "signed_result_sha256 IS NULL OR length(signed_result_sha256) = 64",
            name="ck_reports_signed_result_sha256_length",
        ),
    )


class ReportJob(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Travail de génération d'un rapport, réclamé par un worker dédié (C22).

    Avant C22, le PDF et le ZIP étaient rendus **dans la requête HTTP** : une
    requête qui dure, qu'aucun redémarrage ne peut reprendre, et dont l'échec est
    un 500 sans trace. Le travail est ici une ligne, avec un bail, un compteur de
    tentatives et une date de disponibilité — donc reprennable, traçable et
    dimensionnable en ajoutant des workers, pas des secondes de timeout.

    ``options_json`` conserve la demande **telle qu'elle a été faite** : un rapport
    régénéré six mois plus tard doit pouvoir être reproduit à l'identique, sinon
    la référence de vérification ne vaut que pour l'instant où elle a été émise.
    """

    __tablename__ = "report_jobs"

    analysis_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    report_format: Mapped[str] = mapped_column(String(32), nullable=False)
    options_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ReportJobStatus] = mapped_column(
        _enum(ReportJobStatus, "report_job_status"),
        nullable=False,
        default=ReportJobStatus.QUEUED,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_report_jobs_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_report_jobs_max_attempts_positive"),
        CheckConstraint("max_attempts >= attempt_count", name="ck_report_jobs_attempts_within_max"),
        Index("ix_report_jobs_claim", "organization_id", "status", "available_at"),
    )


class DataSubjectRequest(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Demande d'exercice d'un droit, suivie jusqu'à sa clôture.

    Le manque que cette table comble n'est pas « il n'y a pas de page RGPD » mais
    « personne ne peut prouver qu'une demande a été reçue, traitée dans le délai
    légal, et qu'elle a produit quelque chose ». Deux champs portent cette preuve :

    * ``due_at`` est **calculé** à partir de ``received_at`` (+ un mois, art. 12.3) —
      jamais saisi à la main, sinon il ne vaut rien ;
    * ``outcome_reference`` dit **ce qui a été remis** (l'empreinte de l'export, la
      référence du manifeste d'effacement). Un accord sans référence est un accord
      sans preuve : la base le refuse.
    """

    __tablename__ = "data_subject_requests"

    request_type: Mapped[DataSubjectRight] = mapped_column(
        _enum(DataSubjectRight, "data_subject_right"), nullable=False, index=True
    )
    requester_email: Mapped[str] = mapped_column(String(320), nullable=False)
    requester_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requester_is_member: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    details: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    extension_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extension_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[DataSubjectRequestStatus] = mapped_column(
        _enum(DataSubjectRequestStatus, "data_subject_request_status"),
        nullable=False,
        default=DataSubjectRequestStatus.RECEIVED,
        index=True,
    )
    outcome: Mapped[DataSubjectRequestOutcome | None] = mapped_column(
        _enum(DataSubjectRequestOutcome, "data_subject_request_outcome"), nullable=True
    )
    outcome_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    refusal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    handled_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("due_at > received_at", name="ck_data_subject_requests_due_after_receipt"),
        CheckConstraint(
            "extension_due_at IS NULL OR extension_reason IS NOT NULL",
            name="ck_data_subject_requests_extension_has_reason",
        ),
        CheckConstraint(
            "extension_due_at IS NULL OR extension_due_at > due_at",
            name="ck_data_subject_requests_extension_after_due",
        ),
        CheckConstraint(
            "status NOT IN ('completed', 'refused') OR completed_at IS NOT NULL",
            name="ck_data_subject_requests_closed_has_date",
        ),
        CheckConstraint(
            "status <> 'refused' OR refusal_reason IS NOT NULL",
            name="ck_data_subject_requests_refusal_has_reason",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome = 'refused' OR outcome_reference IS NOT NULL",
            name="ck_data_subject_requests_grant_references_delivery",
        ),
        Index(
            "ix_data_subject_requests_open",
            "organization_id",
            "status",
            "due_at",
        ),
    )


class SupportRequest(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Demande de support déposée depuis l'application, avec son contexte d'écran.

    C15 demandait un bouton de contact « contextualisé à l'écran » et un SLA affiché
    par plan. Les deux sont ici, et d'une façon qui ne peut pas mentir :

    * ``screen`` et ``last_error_code`` sont renseignés **par le client au moment de
      l'envoi** : le support reçoit le contexte, pas une description approximative ;
    * ``first_response_due_at`` est **calculé** à partir du plan réel de l'organisation
      et du délai publié pour la catégorie — un délai saisi à la main ne serait tenu
      par personne ;
    * ``plan_code`` et ``first_response_hours`` sont figés sur la ligne : si le plan
      change ou si la politique évolue, la demande passée reste jugée selon ce qui
      était publié le jour où elle a été déposée.
    """

    __tablename__ = "support_requests"

    category: Mapped[SupportRequestCategory] = mapped_column(
        _enum(SupportRequestCategory, "support_request_category"), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    #: écran ou parcours d'où la demande a été envoyée (ex. « analyse », « rapport »)
    screen: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: dernier code d'erreur affiché à l'utilisateur, s'il y en avait un
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requester_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requester_email: Mapped[str] = mapped_column(String(320), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    first_response_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    first_response_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[SupportRequestStatus] = mapped_column(
        _enum(SupportRequestStatus, "support_request_status"),
        nullable=False,
        default=SupportRequestStatus.OPEN,
        index=True,
    )
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("first_response_hours > 0", name="ck_support_requests_target_positive"),
        CheckConstraint(
            "first_response_due_at > created_at",
            name="ck_support_requests_due_after_creation",
        ),
        CheckConstraint(
            "status = 'open' OR answered_at IS NOT NULL",
            name="ck_support_requests_handled_has_date",
        ),
        CheckConstraint(
            "status <> 'closed' OR resolution IS NOT NULL",
            name="ck_support_requests_closed_has_resolution",
        ),
        Index("ix_support_requests_open", "organization_id", "status", "first_response_due_at"),
    )


class EvidenceRequest(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "evidence_requests"

    supplier_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    claim_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("claims.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[EvidenceRequestStatus] = mapped_column(
        _enum(EvidenceRequestStatus, "evidence_request_status"), nullable=False, default=EvidenceRequestStatus.DRAFT
    )
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    requested_items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    supplier: Mapped[Supplier] = relationship(foreign_keys=[supplier_id])
    product: Mapped[Product | None] = relationship(foreign_keys=[product_id])
    claim: Mapped[Claim | None] = relationship(foreign_keys=[claim_id])
    created_by_user: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])


class AuditEvent(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Append-only business audit event. Hash chaining is per organization."""

    __tablename__ = "audit_events"

    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "event_hash", name="uq_audit_events_organization_hash"),
        CheckConstraint("length(payload_sha256) = 64", name="ck_audit_events_payload_sha256_length"),
        CheckConstraint("length(event_hash) = 64", name="ck_audit_events_event_hash_length"),
        CheckConstraint(
            "previous_event_hash IS NULL OR length(previous_event_hash) = 64",
            name="ck_audit_events_previous_hash_length",
        ),
        Index("ix_audit_events_organization_occurred", "organization_id", "occurred_at"),
    )


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """B2B Partner & Enterprise programmatic access key with rate limits."""

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class LegalHold(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """E-Discovery and forensic legal hold lock preventing data deletion during litigation or official inquiry."""

    __tablename__ = "legal_holds"

    case_reference: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )



# ---------------------------------------------------------------------------
# C13 — plans, quotas, abonnements, journal d'usage et facturation
# ---------------------------------------------------------------------------
#
# Quatre tables, dont deux seulement portent du contenu métier lourd :
#
# * `billing_subscriptions` — une ligne au plus par organisation, créée par le
#   paiement (ou par un événement prestataire), jamais par une lecture ;
# * `billing_usage_events` — journal d'usage **append-only** : les compteurs sont
#   la somme du journal, pas une colonne à incrémenter. Un compteur qui dérive
#   serait un compteur qu'on ne peut pas expliquer à un client qui conteste ;
# * `billing_provider_events` — les événements reçus du prestataire, avec leur
#   identifiant et le résultat de leur application (idempotence vérifiable) ;
# * `billing_invoices` et `billing_checkout_sessions` — le miroir des objets du
#   prestataire, afin que le portail client n'interroge pas un tiers pour
#   afficher une facture.
#
# L'essai de 14 jours n'a **pas** de ligne ici : il se dérive de
# `organizations.created_at` (voir `app.billing.subscriptions`). Une ligne
# « essai » serait une ligne qu'un appel d'API pourrait remettre à zéro.


class BillingSubscriptionStatus(str, Enum):
    """États d'abonnement, et ce qu'ils autorisent réellement.

    `TRIALING` : période d'essai en cours, accès complet, date de fin publiée.
    `ACTIVE` : payé et à jour.
    `PAST_DUE` : paiement échoué ; les lectures restent ouvertes, les actions
    payantes sont refusées avec la raison exacte.
    `CANCELED` : résilié ; l'accès s'arrête à `current_period_end` (dernier jour
    payé), pas au moment du clic.
    """

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class BillingSubscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_subscriptions"

    organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[BillingSubscriptionStatus] = mapped_column(
        _enum(BillingSubscriptionStatus, "billing_subscription_status"),
        nullable=False,
        default=BillingSubscriptionStatus.ACTIVE,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    provider_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_price_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    past_due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Une descente de gamme n'est jamais appliquée au milieu d'une période payée :
    # elle est enregistrée ici et appliquée par l'événement de renouvellement.
    pending_plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_plan_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_provider_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_billing_subscriptions_organization_id"),
        UniqueConstraint(
            "provider", "provider_subscription_id", name="uq_billing_subscriptions_provider_subscription_id"
        ),
        CheckConstraint(
            "current_period_end > current_period_start", name="ck_billing_subscriptions_period_ordered"
        ),
    )


class BillingUsageEvent(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Une ligne = une unité consommée, une fois et une seule.

    ``idempotency_key`` est dérivée de la source (``document:<uuid>``,
    ``ocr:<version_uuid>``, ``seat:<membership_uuid>``) : rejouer un import ne
    double pas la consommation, y compris après un redémarrage du worker.
    """

    __tablename__ = "billing_usage_events"

    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Identifiant exact de la période de comptage (ISO-8601 UTC du début de
    #: période). Une date seule ne suffit pas : deux périodes payées peuvent
    #: commencer le même jour, et confondre leurs clés remettrait à zéro un quota
    #: déjà consommé — ou, pire, l'inverse.
    period_key: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_billing_usage_events_organization_idempotency"
        ),
        Index("ix_billing_usage_events_period", "organization_id", "metric", "period_key"),
        CheckConstraint("quantity > 0", name="ck_billing_usage_events_quantity_positive"),
    )


class BillingProviderEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un événement prestataire reçu sur le webhook, appliqué au plus une fois.

    Cette table n'est volontairement **pas** soumise au RLS par organisation :
    elle est écrite par un appel non authentifié, avant que l'organisation ne
    soit résolue depuis l'identifiant d'abonnement du prestataire. C'est le même
    choix que pour les tables d'identité (`identity_tokens`) : consultation
    service uniquement, jamais par un identifiant de locataire fourni par
    l'appelant.
    """

    __tablename__ = "billing_provider_events"

    organization_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    apply_result: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_event_id", name="uq_billing_provider_events_provider_event_id"
        ),
    )


class BillingInvoice(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Miroir local d'une facture du prestataire (aucune facture n'est émise ici)."""

    __tablename__ = "billing_invoices"

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_invoice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="eur")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hosted_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "provider_invoice_id", name="uq_billing_invoices_provider_invoice_id"),
        CheckConstraint("amount_cents >= 0", name="ck_billing_invoices_amount_non_negative"),
    )


class BillingCheckoutSession(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Un paiement en cours. Aucun accès n'est accordé avant sa confirmation."""

    __tablename__ = "billing_checkout_sessions"

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_session_id", name="uq_billing_checkout_sessions_provider_session_id"
        ),
    )
