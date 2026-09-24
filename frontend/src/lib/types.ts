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
  | "recyclable"
  | "compostable"
  | "chemical_free"
  | "zero_pollution"
  | "recycled_content";

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
  supplier_name?: string | null;
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
  tenant_id?: string;
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

export type AuditHistoryItem = {
  audit_id: string;
  tenant_id?: string;
  created_at_utc: string;
  supplier_name?: string | null;
  product_identifier?: string | null;
  overall_compliance: OverallCompliance;
  risk_score: number;
  violations_count: number;
  conditional_findings_count: number;
  detected_claims_count: number;
  record_hash: string;
  max_fixed_fine_eur?: ApiDecimal | null;
  source_snippet: string;
};

export type AuditHistoryResponse = {
  total: number;
  items: AuditHistoryItem[];
};

export type SupplierSubmission = {
  supplier_name: string;
  product_identifier?: string | null;
  source_text: string;
  context?: Partial<AuditContext>;
  evidence?: Partial<EvidenceDossier>;
};

export type SupplierCompareRequest = {
  audit_ids?: string[];
  submissions?: SupplierSubmission[];
};

export type SupplierComparisonItem = {
  supplier_name: string;
  product_identifier?: string | null;
  audit_id?: string | null;
  overall_compliance: OverallCompliance;
  risk_score: number;
  rank: number;
  recommendation: string;
  recommendation_color: "green" | "amber" | "red" | string;
  violations_count: number;
  violations_summary: string[];
  max_known_fine_eur?: ApiDecimal | null;
  claims_detected: string[];
  procurement_clause: string;
  has_lca_declared: boolean;
  has_ecolabel_declared: boolean;
};

export type SupplierCompareResponse = {
  evaluated_at: string;
  suppliers_count: number;
  ranked_suppliers: SupplierComparisonItem[];
  best_supplier?: string | null;
  benchmark_summary: string;
};

export type CatalogItemInput = {
  sku: string;
  title?: string;
  text: string;
  surface?: Surface;
  category?: string | null;
  supplier_name?: string | null;
  has_lca?: boolean;
  ecolabel_license?: string | null;
};

export type CatalogBatchRequest = {
  items: CatalogItemInput[];
  jurisdiction?: string;
  as_of_date?: string | null;
  consumer_facing?: boolean;
};

export type CatalogItemResult = {
  sku: string;
  title: string;
  supplier_name?: string | null;
  overall_compliance: OverallCompliance;
  risk_score: number;
  detected_claims_count: number;
  violations_count: number;
  fines_ceiling_eur: number;
  summary: string;
  claims: string[];
};

export type CatalogBatchResponse = {
  total_items: number;
  compliant_items: number;
  non_compliant_items: number;
  review_required_items: number;
  total_fines_ceiling_eur: number;
  compliance_rate_pct: number;
  average_risk_score: number;
  results: CatalogItemResult[];
};

export type TenantResponse = {
  id: string;
  name: string;
  slug: string;
  tier: string;
  created_at_utc: string;
  is_active: boolean;
  api_keys_count: number;
  total_audits_count: number;
};

export type ApiKeyCreatedResponse = {
  id: string;
  name: string;
  key: string;
  key_prefix: string;
  scopes: string[];
  created_at_utc: string;
};

export type TenantWithKeyResponse = {
  organization: TenantResponse;
  initial_api_key: ApiKeyCreatedResponse;
};

export type ApiKeyItem = {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  created_at_utc: string;
  last_used_at_utc: string | null;
  is_active: boolean;
};

export type ApiKeyListResponse = {
  total: number;
  keys: ApiKeyItem[];
};
