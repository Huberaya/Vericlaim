export type AuthRole = {
  code: "owner" | "admin" | "analyst" | "viewer";
  name: string;
  permissions: string[];
};

export type Organization = {
  id: string;
  name: string;
  slug: string;
  status: string;
  data_region: string;
};

export type OrganizationMembership = {
  id: string;
  organization: Organization;
  role: AuthRole;
  status: string;
  activated_at: string | null;
};

export type CurrentUser = {
  id: string;
  email: string;
  display_name: string | null;
  status: string;
};

export type AuthSession = {
  authenticated: true;
  user: CurrentUser;
  active_organization_id: string | null;
  memberships: OrganizationMembership[];
};

export type AuthStatus = {
  oidc_configured: boolean;
  authentication_required: true;
};

export type CatalogProductLifecycle = "active" | "inactive" | "discontinued";

export type CatalogSupplier = {
  id: string;
  legal_name: string;
  trading_name: string | null;
  external_reference: string | null;
  country_code: string | null;
  contact_email: string | null;
  metadata: Record<string, unknown>;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CatalogProduct = {
  id: string;
  supplier_id: string;
  reference: string;
  name: string;
  category: string | null;
  country_of_sale: string | null;
  lifecycle_status: CatalogProductLifecycle;
  metadata: Record<string, unknown>;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CatalogSupplierList = { items: CatalogSupplier[]; next_cursor: string | null };
export type CatalogProductList = { items: CatalogProduct[]; next_cursor: string | null };
export type CatalogSupplierCreate = { supplier: CatalogSupplier; idempotent_replay: boolean };
export type CatalogProductCreate = { product: CatalogProduct; idempotent_replay: boolean };

export type DocumentType =
  | "supplier_declaration"
  | "product_sheet"
  | "marketing_asset"
  | "certificate"
  | "lca_report"
  | "environmental_declaration"
  | "lab_report"
  | "other";

export type DocumentStatus = "quarantined" | "uploaded" | "processing" | "ready" | "failed" | "archived" | "deleted";
export type DocumentUploadStatus = "pending_upload" | "uploaded" | "scanning" | "clean" | "rejected" | "failed" | "expired";
export type ExtractionStatus = "pending" | "running" | "completed" | "failed" | "review_required";
export type DocumentExtractionJobStatus = "queued" | "running" | "completed" | "failed";

export type DocumentExtractionJob = {
  status: DocumentExtractionJobStatus;
  attempt_count: number;
  max_attempts: number;
  available_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
};

export type StoredDocument = {
  id: string;
  document_key: string;
  title: string;
  document_type: DocumentType;
  status: DocumentStatus;
  supplier_id: string | null;
  product_id: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
};

export type StoredDocumentVersion = {
  id: string;
  version_number: number;
  source_filename: string;
  content_type: string;
  sha256: string;
  size_bytes: number;
  page_count: number | null;
  extraction_status: ExtractionStatus;
  extraction_error_code: string | null;
  extraction_completed_at: string | null;
  extraction_job: DocumentExtractionJob | null;
  created_at: string;
};

export type DocumentUpload = {
  id: string;
  document_id: string;
  status: DocumentUploadStatus;
  source_filename: string;
  declared_content_type: string;
  declared_size_bytes: number;
  expires_at: string;
  uploaded_sha256: string | null;
  scan_engine: string | null;
  scan_completed_at: string | null;
  scan_error_code: string | null;
  finalized_document_version_id: string | null;
};

export type DocumentUploadInstruction = {
  upload: DocumentUpload;
  upload_url: string;
  upload_fields: Record<string, string>;
  upload_method: "POST";
  max_size_bytes: number;
};

export type DocumentUploadCompletion = {
  upload: DocumentUpload;
  document: StoredDocument;
  version: StoredDocumentVersion | null;
  detail?: string | null;
};

export type StoredDocumentDetail = {
  document: StoredDocument;
  versions: StoredDocumentVersion[];
  uploads: DocumentUpload[];
};

export type DocumentSegment = {
  id: string;
  sequence_number: number;
  page_number: number | null;
  segment_type: "title" | "paragraph" | "table" | "table_cell" | "footnote" | "image_ocr" | "other";
  text: string;
  start_offset: number | null;
  end_offset: number | null;
  bounding_box: Record<string, unknown> | null;
  source_sha256: string;
};

export type DocumentVersionSegments = {
  version: StoredDocumentVersion;
  segments: DocumentSegment[];
};

export type DocumentExtractionRetry = {
  version: StoredDocumentVersion;
  extraction_job: DocumentExtractionJob;
};

export type DocumentDownload = {
  url: string;
  expires_at: string;
};

/** Persistent C5 deterministic claim-detection workflow. No legal verdict or score is represented here. */
export type PersistentAnalysisStatus =
  | "draft"
  | "queued"
  | "extracting"
  | "detecting_claims"
  | "checking_evidence"
  | "scoring"
  | "completed"
  | "failed"
  | "cancelled";
export type AnalysisDetectionJobStatus = "queued" | "running" | "completed" | "failed";

export type PersistentAnalysisDetectionJob = {
  status: AnalysisDetectionJobStatus;
  attempt_count: number;
  max_attempts: number;
  available_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
};

export type PersistentAnalysis = {
  id: string;
  analysis_key: string;
  status: PersistentAnalysisStatus;
  supplier_id: string | null;
  product_id: string | null;
  requested_by_user_id: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type PersistentAnalysisVersion = {
  id: string;
  analysis_id: string;
  version_number: number;
  status: PersistentAnalysisStatus;
  engine_version: string;
  rulebook_version: string;
  input_manifest_sha256: string;
  result_sha256: string | null;
  input_manifest: Record<string, unknown> | null;
  result: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
};

export type PersistentClaimCitation = {
  document_segment_id: string | null;
  document_version_id: string | null;
  document_id: string | null;
  page_number: number | null;
  segment_type: DocumentSegment["segment_type"] | null;
  segment_start_offset: number | null;
  segment_end_offset: number | null;
  source_sha256: string | null;
};

export type PersistentClaim = {
  id: string;
  analysis_version_id: string;
  document_segment_id: string | null;
  claim_type: string;
  category: string;
  claim_text: string;
  normalized_text: string | null;
  language: string | null;
  start_offset: number | null;
  end_offset: number | null;
  source: "deterministic" | "ai_candidate" | "human";
  confidence_score: number | null;
  status: "detected" | "confirmed" | "dismissed" | "review_required";
  detector_version: string | null;
  attributes: Record<string, unknown>;
  citation: PersistentClaimCitation;
  created_at: string;
};

export type PersistentAnalysisEnqueueResponse = {
  analysis: PersistentAnalysis;
  version: PersistentAnalysisVersion;
  detection_job: PersistentAnalysisDetectionJob;
  idempotent_replay: boolean;
  disclaimer: string;
};

export type PersistentAnalysisDetail = {
  analysis: PersistentAnalysis;
  versions: PersistentAnalysisVersion[];
  latest_version: PersistentAnalysisVersion | null;
  disclaimer: string;
};

export type PersistentAnalysisVersionDetail = {
  analysis: PersistentAnalysis;
  version: PersistentAnalysisVersion;
  detection_job: PersistentAnalysisDetectionJob | null;
  claims: PersistentClaim[];
  disclaimer: string;
};

export type Surface =
  | "product_label"
  | "packaging"
  | "advertisement"
  | "online_store"
  | "supplier_contract"
  | "unknown";

export type ClaimType =
  | "biodegradable"
  | "nature_friendly"
  | "generic_environmental"
  | "carbon_neutrality"
  | "comparative"
  | "quantified_climate"
  | "recyclable";

/** UI classification derived from the engine's formal verdict, not an API field. */
export type ViolationSeverity = "CRITICAL" | "WARNING" | "INFO";
export type EngineSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type LegalForce =
  | "BINDING_FR"
  | "EU_DIRECTIVE_DATE_GATED"
  | "PROPOSAL_ONLY"
  | "VOLUNTARY_STANDARD"
  | "INTERNAL_EVIDENCE_CONTROL";
export type Verdict =
  | "STRICTLY_PROHIBITED"
  | "NON_COMPLIANT"
  | "CONDITIONAL_REJECT"
  | "REVIEW_REQUIRED"
  | "COMPLIANT"
  | "NOT_APPLICABLE"
  | "UPCOMING"
  | "ADVISORY_ONLY";
export type OverallCompliance =
  | "COMPLIANT"
  | "NON_COMPLIANT"
  | "CONDITIONAL_REJECT"
  | "REVIEW_REQUIRED"
  | "UPCOMING_REQUIREMENTS"
  | "NO_CLAIMS_DETECTED";
export type EvidenceStatus =
  | "NOT_PROVIDED"
  | "INCOMPLETE"
  | "METADATA_COMPLETE"
  | "VERIFIED"
  | "NOT_INDEPENDENTLY_VERIFIED"
  | "INVALID";
export type ApiDecimal = number | string;

export type EvidenceBase = {
  evidence_id?: string | null;
  reference?: string | null;
  file_name?: string | null;
  file_sha256?: string | null;
  product_scope?: string | null;
  claim_excerpt?: string | null;
  issued_on?: string | null;
  expires_on?: string | null;
};

export type LcaEvidence = EvidenceBase & {
  kind: "lca_report";
  standard?: string | null;
  functional_unit?: string | null;
  system_boundary?: string | null;
  impact_categories?: string[];
  comparative?: boolean;
  comparison_product?: string | null;
  same_functional_unit?: boolean;
  same_system_boundary?: boolean;
};

export type EcolabelEvidence = EvidenceBase & {
  kind: "ecolabel_certificate";
  scheme: "EU_ECOLABEL" | "EN_ISO_14024_TYPE_I" | "OTHER";
  license_number: string;
  product_identifier?: string | null;
  product_category?: string | null;
  issuer?: string | null;
};

export type RecyclingRouteEvidence = EvidenceBase & {
  kind: "recycling_route";
  material_or_component?: string | null;
  territories: string[];
  collection_available: boolean;
  sorting_available: boolean;
  consumer_access: boolean;
  industrial_processing_available: boolean;
  coverage_percent?: ApiDecimal | null;
};

export type GHGInventoryEvidence = EvidenceBase & {
  kind: "ghg_inventory";
  standard?: string | null;
  includes_direct_emissions: boolean;
  includes_indirect_emissions: boolean;
  product_lifecycle_scope?: string | null;
  public_disclosure_url?: string | null;
};

export type GHGReductionPlanEvidence = EvidenceBase & {
  kind: "ghg_reduction_plan";
  avoidance_prioritised: boolean;
  reduction_before_compensation: boolean;
  annual_quantified_targets: boolean;
  public_disclosure_url?: string | null;
};

export type CarbonOffsetEvidence = EvidenceBase & {
  kind: "carbon_offset";
  standard_or_registry?: string | null;
  retirement_reference?: string | null;
  vintage_year?: number | null;
  quantity_tco2e?: ApiDecimal | null;
  residual_emissions_reference?: string | null;
};

export type OtherEvidence = EvidenceBase & {
  kind: "other";
  description?: string | null;
};

export type EvidenceItem =
  | LcaEvidence
  | EcolabelEvidence
  | RecyclingRouteEvidence
  | GHGInventoryEvidence
  | GHGReductionPlanEvidence
  | CarbonOffsetEvidence
  | OtherEvidence;

export type EvidenceDossier = {
  items: EvidenceItem[];
  legal_person: boolean;
  average_annual_turnover_eur?: ApiDecimal | null;
  advertising_spend_eur?: ApiDecimal | null;
};

export type AuditContext = {
  as_of_date: string;
  jurisdiction: string;
  surface: Surface;
  consumer_facing: boolean;
  product_identifier?: string | null;
  product_category?: string | null;
  operation_spend_eur?: ApiDecimal | null;
};

export type EvaluationRequest = {
  source_text: string;
  context: AuditContext;
  evidence: EvidenceDossier;
};

export type EvidenceCheck = {
  check_name: string;
  status: EvidenceStatus;
  evidence_ids: string[];
  required_fields: string[];
  missing_fields: string[];
  detail: string;
  independently_verified: boolean;
};

export type ReasoningStep = {
  step: number;
  code: string;
  finding: string;
  legal_significance: string;
};

export type Remediation = {
  buyer_explanation: string;
  recommended_rewrite: string;
  supplier_contract_clause: string;
  required_actions: string[];
};

export type SanctionProfile = {
  mechanism: string;
  authority: string;
  legal_basis: string;
  max_natural_person_eur: ApiDecimal | null;
  max_legal_person_eur: ApiDecimal | null;
  may_scale_to_advertising_spend: boolean;
  amount_is_automatic: boolean;
  notes: string;
};

/** One typed engine decision for one extracted claim and one formal rule. */
export type ClaimEvaluation = {
  claim_id: string;
  claim_text: string;
  claim_type: ClaimType;
  start_offset: number;
  end_offset: number;
  trigger_text: string;
  rule_id: string;
  rule_title: string;
  law_reference: string;
  source_urls: string[];
  legal_force: LegalForce;
  severity: EngineSeverity;
  verdict: Verdict;
  is_legal_violation: boolean;
  safe_harbor_applicable: boolean;
  safe_harbor_reason: string | null;
  required_evidence: string[];
  evidence_checks: EvidenceCheck[];
  reasoning_steps: ReasoningStep[];
  remediation: Remediation;
  sanction: SanctionProfile | null;
  legal_caveat: string | null;
};

export type LegalAssessment = ClaimEvaluation;

export type ExposureCategory = "ADMINISTRATIVE" | "PENAL" | "CIVIL" | "COMMERCIAL";
export type ExposureItem = {
  category: ExposureCategory;
  title: string;
  legal_basis: string | null;
  max_natural_person_eur: ApiDecimal | null;
  max_legal_person_eur: ApiDecimal | null;
  calculated_amount_eur: ApiDecimal | null;
  may_scale_to_advertising_spend: boolean;
  conditional: boolean;
  note: string;
};

export type ExposureMatrix = {
  items: ExposureItem[];
  max_known_fixed_fine_eur: ApiDecimal | null;
  max_known_fixed_fine_for: string;
  amount_is_cumulative: boolean;
  civil_risk: string[];
  calculation_notes: string[];
};

export type AuditTrail = {
  audit_id: string;
  engine_version: string;
  rulebook_version: string;
  evaluated_at_utc: string;
  as_of_date: string;
  source_sha256: string;
  document_sha256: string | null;
  evidence_manifest_sha256: string;
  report_sha256: string;
  previous_record_hash: string | null;
  record_hash: string;
  extraction_method: string;
  limitations: string[];
};

export type RegulatoryAuditResponse = {
  extracted_source_text: string;
  overall_compliance: OverallCompliance;
  risk_score: number;
  legal_exposure_estimate: string;
  violations_count: number;
  conditional_findings_count: number;
  detected_claims_count: number;
  evaluations: ClaimEvaluation[];
  exposure_matrix: ExposureMatrix;
  audit_trail: AuditTrail;
};

export type EvaluationResponse = RegulatoryAuditResponse;

export function violationSeverity(evaluation: ClaimEvaluation): ViolationSeverity {
  if (
    evaluation.is_legal_violation ||
    evaluation.verdict === "STRICTLY_PROHIBITED" ||
    evaluation.verdict === "NON_COMPLIANT"
  ) return "CRITICAL";
  if (
    evaluation.verdict === "CONDITIONAL_REJECT" ||
    evaluation.verdict === "REVIEW_REQUIRED" ||
    evaluation.verdict === "UPCOMING"
  ) return "WARNING";
  return "INFO";
}
