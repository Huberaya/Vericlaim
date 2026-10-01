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

// C23 — lecture d'un tableau de fiche technique. Le moteur rend une lecture
// déterministe (colonne critère / valeur / unité, offsets de cellule), jamais une
// conclusion sur le chiffre lu.
export type ClaimTableCell = {
  row_index: number | null;
  column_index: number | null;
  role: string | null;
  text: string | null;
  start_offset: number | null;
  end_offset: number | null;
};

export type ClaimTableEntry = {
  criterion: string | null;
  value: string | null;
  numeric_value: string | null;
  unit: string | null;
  row_index: number | null;
};

export type ClaimTableCitation = {
  page_number: number | null;
  columns: string[];
  has_header: boolean;
  cell: ClaimTableCell;
  row_entry: ClaimTableEntry | null;
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
  // Optionnel : une allégation détectée avant C23 n'en porte pas, et l'absence est
  // affichée comme une absence — jamais complétée à l'écran.
  table_citation?: ClaimTableCitation | null;
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
  // C10: rubrique déterministe publiée avec ses facteurs. Optionnels car une
  // ligne antérieure à C10 n'en porte pas — on ne l'invente jamais à l'affichage.
  confidence_level?: "high" | "medium" | "low" | "human_review_required" | null;
  confidence_factors?: Array<{ code: string; effect: number; detail: string }>;
  confidence_basis?: string | null;
  confidence_rubric_version?: string | null;
  review_reasons?: string[];
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

export type PersistentEvidenceType =
  | "certificate"
  | "lca_report"
  | "environmental_declaration"
  | "lab_report"
  | "recycling_route"
  | "ghg_inventory"
  | "ghg_reduction_plan"
  | "carbon_offset"
  | "standard"
  | "other";

export type PersistentEvidenceStatus =
  | "pending"
  | "present"
  | "partial"
  | "missing"
  | "expired"
  | "out_of_scope"
  | "verified"
  | "rejected";

export type PersistentEvidenceRelation =
  | "supports"
  | "partially_supports"
  | "contradicts"
  | "not_related"
  | "review_required";

export type PersistentEvidence = {
  id: string;
  evidence_type: PersistentEvidenceType;
  status: PersistentEvidenceStatus;
  reference: string | null;
  issuer: string | null;
  issued_on: string | null;
  expires_on: string | null;
  product_scope: string | null;
  supplier_id: string | null;
  product_id: string | null;
  document_version_id: string | null;
  certificate_id: string | null;
  evidence_metadata: Record<string, unknown>;
  verified_at: string | null;
  verified_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type PersistentEvidenceList = {
  items: PersistentEvidence[];
  next_cursor: string | null;
};

export type PersistentEvidenceCreateRequest = {
  evidence_type: PersistentEvidenceType;
  reference?: string | null;
  issuer?: string | null;
  issued_on?: string | null;
  expires_on?: string | null;
  product_scope?: string | null;
  supplier_id?: string | null;
  product_id?: string | null;
  document_version_id?: string | null;
  certificate_id?: string | null;
  status?: PersistentEvidenceStatus;
  evidence_metadata?: Record<string, unknown>;
};

export type PersistentEvidenceLink = {
  id: string;
  claim_id: string;
  evidence_id: string;
  relation: PersistentEvidenceRelation;
  // C17 — `coverage_status` est la **déclaration** d'un relecteur (`pending` = aucune).
  // Le constat de la pièce (périmètre, validité, famille d'allégation) est `observed_state`.
  coverage_status: PersistentEvidenceStatus;
  observed_state?: "missing" | "expired" | "out_of_scope" | "not_covering" | "partial" | "covered" | null;
  validity_as_of: string | null;
  confidence_score: number | null;
  rationale: string | null;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  created_at: string;
  evidence?: PersistentEvidence | null;
};

export type ClaimWithEvidenceLinks = {
  claim: PersistentClaim;
  evidence_links: PersistentEvidenceLink[];
  coverage_status: PersistentEvidenceStatus;
  is_sufficient: boolean;
  explanation: string;
};

export type EvidenceMatrix = {
  analysis_id: string;
  analysis_version_id: string;
  version_number: number;
  total_claims: number;
  claims_with_evidence: number;
  claims_missing_evidence: number;
  claims_expired_evidence: number;
  claims_out_of_scope_evidence: number;
  matrix_rows: ClaimWithEvidenceLinks[];
  disclaimer: string;
};

export type ValidationDecision = "pending" | "validated" | "contested";

export type Validation = {
  id: string;
  analysis_version_id: string;
  claim_id: string;
  decision: ValidationDecision;
  reviewer_user_id: string;
  reviewer_display_name: string | null;
  comment: string | null;
  rationale: string | null;
  decided_at: string;
  created_at: string;
};

export type ValidationCreateRequest = {
  analysis_version_id: string;
  claim_id: string;
  decision: ValidationDecision;
  comment?: string | null;
  rationale?: string | null;
};

export type EvidenceRequestStatus =
  | "draft"
  | "sent"
  | "received"
  | "fulfilled"
  | "cancelled"
  | "overdue";

export type EvidenceRequestItem = {
  type: string;
  name: string;
  notes?: string | null;
};

export type EvidenceRequest = {
  id: string;
  supplier_id: string;
  supplier_name: string | null;
  product_id: string | null;
  product_name: string | null;
  claim_id: string | null;
  claim_text: string | null;
  status: EvidenceRequestStatus;
  subject: string;
  message: string;
  requested_items: EvidenceRequestItem[];
  due_at: string | null;
  sent_at: string | null;
  last_reminded_at: string | null;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
};

export type EvidenceRequestList = {
  items: EvidenceRequest[];
  next_cursor: string | null;
};

export type EvidenceRequestCreateRequest = {
  supplier_id: string;
  product_id?: string | null;
  claim_id?: string | null;
  subject: string;
  message: string;
  requested_items?: EvidenceRequestItem[];
  due_at?: string | null;
};

export type EvidenceRequestUpdateRequest = {
  status?: EvidenceRequestStatus;
  subject?: string;
  message?: string;
  requested_items?: EvidenceRequestItem[];
  due_at?: string | null;
};

export type TemplateGenerationRequest = {
  claim_id?: string | null;
  supplier_id?: string | null;
  product_id?: string | null;
  target_evidence_type?: PersistentEvidenceType | null;
};

export type TemplateGenerationResponse = {
  subject: string;
  message: string;
  requested_items: EvidenceRequestItem[];
  suggested_due_days: number;
};

// ---------------------------------------------------------------------------
// Chantier 7 — Regulatory Governance & Rule Book Models
// ---------------------------------------------------------------------------

export type Jurisdiction = "FR" | "EU" | "INTERNATIONAL";

export type LegalStatus =
  | "in_force"
  | "pending_transposition"
  | "proposal"
  | "superseded"
  | "repealed";

export type ReviewStatus =
  | "approved_legal"
  | "under_review"
  | "draft"
  | "deprecated";

export type ConfidenceLevel = "high" | "medium" | "low";

export type OfficialCitation = {
  article: string;
  source_title: string;
  text_excerpt: string;
  url: string;
  effective_date: string | null;
};

export type SafeHarborSchema = {
  safe_harbor_id: string;
  title: string;
  evidence_kind: string;
  conditions: string[];
};

export type RuleGovernanceReview = {
  review_status: ReviewStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  confidence_level: ConfidenceLevel;
  legal_notes: string | null;
};

export type RegulatoryRuleSummary = {
  rule_id: string;
  title: string;
  legal_reference: string;
  jurisdiction: Jurisdiction;
  legal_status: LegalStatus;
  legal_force: string;
  severity: string;
  rule_kind: string;
  claim_types: string[];
  effective_from: string | null;
  transposition_deadline: string | null;
  confidence_level: ConfidenceLevel;
  review_status: ReviewStatus;
  has_safe_harbors: boolean;
  has_sanctions: boolean;
  incomplete_coverage_warning: string | null;
};

export type RegulatoryRuleDetail = {
  rule_id: string;
  title: string;
  legal_reference: string;
  jurisdiction: Jurisdiction;
  legal_status: LegalStatus;
  legal_force: string;
  severity: string;
  rule_kind: string;
  scope: string;
  claim_types: string[];
  official_citations: OfficialCitation[];
  source_urls: string[];
  required_evidence: string[];
  safe_harbors: SafeHarborSchema[];
  surfaces: string[];
  effective_from: string | null;
  effective_until: string | null;
  transposition_deadline: string | null;
  sanction: SanctionProfile | null;
  priority: number;
  notes: string[];
  governance_review: RuleGovernanceReview;
  incomplete_coverage_warning: string | null;
  disclaimer: string;
};

export type RuleBookSummaryResponse = {
  rulebook_version: string;
  release_date: string;
  sha256_fingerprint: string;
  total_rules: number;
  jurisdiction_breakdown: Record<string, number>;
  legal_status_breakdown: Record<string, number>;
  coverage_warnings_count: number;
  governance_statement: string;
  disclaimer: string;
};

export type RuleFieldDiff = {
  field: string;
  old_value: unknown;
  new_value: unknown;
};

export type RuleDiffItem = {
  rule_id: string;
  diff_type: "added" | "modified" | "deprecated";
  title: string;
  summary: string;
  field_diffs?: RuleFieldDiff[];
};

export type RuleBookChangelogEntry = {
  version: string;
  release_date: string;
  title: string;
  description: string;
  diff_items: RuleDiffItem[];
};

export type RuleReviewSubmissionRequest = {
  review_status: ReviewStatus;
  confidence_level: ConfidenceLevel;
  legal_notes?: string | null;
};

// ---------------------------------------------------------------------------
// Chantier 8 — B2B Pilot Pack Models
// ---------------------------------------------------------------------------

export type PilotOverviewKPIs = {
  total_suppliers: number;
  total_products: number;
  total_documents: number;
  total_analyses: number;
  total_claims_detected: number;
  claims_validated: number;
  claims_contested: number;
  claims_pending_review: number;
  claims_with_sufficient_evidence: number;
  claims_missing_evidence: number;
  claims_expired_evidence: number;
  pending_evidence_requests: number;
  overdue_evidence_requests: number;
  global_compliance_rate_percent: number;
  critical_risk_claims_count: number;
};

export type SupplierRiskSummary = {
  supplier_id: string;
  supplier_name: string;
  country_code: string | null;
  products_count: number;
  claims_count: number;
  missing_evidence_count: number;
  pending_requests_count: number;
  risk_level: "high" | "medium" | "low";
};

export type PilotOverviewResponse = {
  organization_id: string;
  organization_name: string;
  kpis: PilotOverviewKPIs;
  top_risk_suppliers: SupplierRiskSummary[];
  rulebook_version: string;
  generated_at: string;
  disclaimer: string;
};

export type PreAuditFinding = {
  claim_text: string;
  category: string;
  claim_type: string;
  severity: string;
  legal_basis: string;
  coverage_status: string;
  validation_decision: string;
  reviewer_comment: string | null;
  remediation_advice: string;
};

export type PreAuditReportResponse = {
  report_id: string;
  organization_id: string;
  organization_name: string;
  generated_at: string;
  as_of_date: string;
  jurisdiction: string;
  rulebook_version: string;
  rulebook_sha256: string;
  summary_kpis: PilotOverviewKPIs;
  findings: PreAuditFinding[];
  remediation_summary: string[];
  audit_trail_signature: string;
  legal_disclaimer: string;
};

export type CatalogImportItem = {
  supplier_legal_name: string;
  supplier_country?: string | null;
  supplier_email?: string | null;
  product_reference?: string | null;
  product_name?: string | null;
  product_category?: string | null;
};

export type CatalogImportResult = {
  suppliers_created: number;
  suppliers_reused: number;
  products_created: number;
  products_reused: number;
  errors: string[];
};

export type RetentionPolicyResponse = {
  organization_id: string;
  documents_retention_years: number;
  audit_trail_retention_years: number;
  evidence_archive_retention_years: number;
  gdpr_contact_email: string;
  encryption_standard: string;
  storage_region: string;
  export_formats_supported: string[];
  last_policy_review: string;
};

// ---------------------------------------------------------------------------
// Chantier 9 — Enterprise Industrialization Models
// ---------------------------------------------------------------------------

export type ApiKeyCreateRequest = {
  name: string;
  scopes: string[];
  rate_limit_per_minute?: number;
  expires_in_days?: number | null;
};

export type ApiKeyCreatedResponse = {
  id: string;
  name: string;
  prefix: string;
  raw_api_key: string;
  scopes: string[];
  rate_limit_per_minute: number;
  expires_at: string | null;
  created_at: string;
};

export type ApiKeySummary = {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  rate_limit_per_minute: number;
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
};

export type EnterpriseMetricsResponse = {
  uptime_seconds: number;
  /** derived from the readiness probes; `status_reasons` says why */
  service_status: "healthy" | "degraded" | "unavailable" | string;
  /** empty means nothing degraded was observed; never a status word without proof */
  status_reasons: string[];
  database_status: string;
  storage_status: string;
  workers_status: string;
  /** null when the instance cannot measure it; the name appears in `not_measured` */
  active_tenants_count: number | null;
  total_analyses_completed: number;
  average_analysis_latency_ms: number | null;
  total_api_requests: number;
  error_rate_percent: number;
  open_alerts_count: number;
  memory_usage_mb: number | null;
  cpu_utilization_percent: number | null;
  /** audit events of the requesting organization */
  tenant_audit_events: number;
  /** fields with no honest value on this instance */
  not_measured: string[];
  metrics_note: string;
  timestamp: string;
};

export type EnterpriseAlert = {
  id: string;
  severity: "info" | "warning" | "critical";
  category: "security" | "quota" | "sso" | "system" | "queue" | "integrity" | string;
  title: string;
  message: string;
  occurred_at: string;
  /** always false today: this code base stores no acknowledgement, so claiming one
   *  would be an invention. A human acknowledges; the field says whether that
   *  happened, never that it should have. */
  is_acknowledged: boolean;
};

export type LegalHoldRequest = {
  case_reference: string;
  reason: string;
  expires_at?: string | null;
};

export type LegalHoldResponse = {
  id: string;
  case_reference: string;
  reason: string;
  is_active: boolean;
  created_by: string;
  created_at: string;
  expires_at: string | null;
};

// ---------------------------------------------------------------------------
// Chantier 10 — Regulatory PDF Reporting & Opposable Dossier
// ---------------------------------------------------------------------------

export type PdfExportOptions = {
  document_title?: string;
  product_identifier?: string;
  surface?: string;
  include_evidence_matrix?: boolean;
  include_remediation_clauses?: boolean;
};

// ---------------------------------------------------------------------------
// Chantier 11 — Tamper-Evident Audit Chain & Ledger Integrity
// ---------------------------------------------------------------------------

export type AuditEventLog = {
  id: string;
  organization_id: string;
  actor_user_id: string | null;
  entity_type: string;
  entity_id: string | null;
  action: string;
  occurred_at: string;
  request_id: string | null;
  payload_json: Record<string, unknown>;
  payload_sha256: string;
  previous_event_hash: string | null;
  event_hash: string;
};

export type AuditChainVerification = {
  is_valid: boolean;
  organization_id: string;
  total_events: number;
  head_event_hash: string | null;
  genesis_event_hash: string | null;
  first_event_at: string | null;
  last_event_at: string | null;
  tampered_event_id: string | null;
  error_detail: string | null;
  verified_at: string;
};

export type AuditIntegrityCertificate = {
  organization_id: string;
  organization_name: string;
  certificate_id: string;
  chain_length: number;
  head_event_hash: string;
  merkle_digest: string;
  verification_status: string;
  certified_at: string;
  issuer: string;
  legal_disclaimer: string;
};

// ---------------------------------------------------------------------------
// C13 — facturation : catalogue, abonnement, compteurs
// ---------------------------------------------------------------------------

export type PlanQuotas = {
  documents_per_month: number;
  ocr_pages_per_month: number;
  seats: number;
  retention_months: number;
};

export type PlanRead = {
  code: string;
  name: string;
  tagline: string;
  price_cents_per_month_excl_vat: number | null;
  price_label: string;
  quotas: PlanQuotas;
  entitlements: string[];
  limits_are_reference_values: boolean;
};

export type PlanCatalogue = {
  catalogue_version: string;
  pricing_status: string;
  pricing_confirmed: boolean;
  overage_policy: string;
  provider: string;
  collects_money: boolean;
  selling_enabled: boolean;
  plans: PlanRead[];
  trial_plan_code: string;
  trial_days: number;
  notes: string[];
};

export type BillingSubscription = {
  organization_id: string;
  plan_code: string | null;
  plan_name: string | null;
  status: string;
  granted: boolean;
  reason: string;
  provider: string | null;
  trial_ends_at: string | null;
  trial_derived_from_organization_creation: boolean;
  current_period_start: string;
  current_period_end: string;
  cancel_at_period_end: boolean;
  pending_plan_code: string | null;
  pending_plan_effective_at: string | null;
  seats_used: number;
  seats_limit: number | null;
  quotas: PlanQuotas | null;
  overage_policy: string;
  available_actions: string[];
  upgrade_options: PlanRead[];
  downgrade_options: PlanRead[];
};

export type BillingUsageLine = {
  metric: string;
  label: string;
  used: number;
  limit: number | null;
  remaining: number | null;
  exceeded: boolean;
  counted: boolean;
};

export type BillingUsage = {
  organization_id: string;
  plan_code: string | null;
  period_start: string;
  period_end: string;
  resets_at: string;
  overage_policy: string;
  lines: BillingUsageLine[];
  note: string;
};
