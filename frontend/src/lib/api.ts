import { csrfHeaders } from "@/lib/csrf";
import type {
  AuditContext,
  CatalogProduct,
  CatalogProductCreate,
  CatalogProductLifecycle,
  CatalogSupplier,
  CatalogSupplierCreate,
  DocumentDownload,
  DocumentExtractionRetry,
  DocumentType,
  DocumentUploadCompletion,
  DocumentUploadInstruction,
  DocumentVersionSegments,
  EvidenceMatrix,
  PersistentAnalysisDetail,
  PersistentAnalysisEnqueueResponse,
  PersistentAnalysisVersionDetail,
  PersistentEvidence,
  PersistentEvidenceCreateRequest,
  PersistentEvidenceLink,
  PersistentEvidenceList,
  PersistentEvidenceRelation,
  PersistentEvidenceStatus,
  PersistentEvidenceType,
  StoredDocumentDetail,
  EvidenceDossier,
  EvidenceItem,
  EvaluationRequest,
  EvidenceRequest,
  EvidenceRequestCreateRequest,
  EvidenceRequestList,
  EvidenceRequestStatus,
  EvidenceRequestUpdateRequest,
  LcaEvidence,
  RegulatoryAuditResponse,
  StoredDocument,
  TemplateGenerationRequest,
  TemplateGenerationResponse,
  Validation,
  ValidationCreateRequest,
  ConfidenceLevel,
  Jurisdiction,
  LegalStatus,
  RegulatoryRuleDetail,
  RegulatoryRuleSummary,
  RuleBookChangelogEntry,
  RuleBookSummaryResponse,
  RuleReviewSubmissionRequest,
  CatalogImportItem,
  CatalogImportResult,
  PilotOverviewResponse,
  PreAuditReportResponse,
  RetentionPolicyResponse,
  ApiKeyCreateRequest,
  ApiKeyCreatedResponse,
  ApiKeySummary,
  EnterpriseAlert,
  EnterpriseMetricsResponse,
  LegalHoldRequest,
  LegalHoldResponse,
  PdfExportOptions,
  AuditEventLog,
  AuditChainVerification,
  AuditIntegrityCertificate,
} from "@/lib/types";

export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

export type AuditOptions = {
  context?: Partial<AuditContext>;
  evidence?: Partial<EvidenceDossier>;
  additionalEvidence?: EvidenceItem[];
};

export type SecureDocumentUploadOptions = {
  title?: string;
  documentType?: DocumentType;
  tags?: string[];
  supplierId?: string | null;
  productId?: string | null;
};

export type CatalogSupplierInput = {
  legal_name: string;
  trading_name?: string | null;
  external_reference?: string | null;
  country_code?: string | null;
  contact_email?: string | null;
  metadata?: Record<string, unknown>;
};

export type CatalogProductInput = {
  supplier_id: string;
  reference: string;
  name: string;
  category?: string | null;
  country_of_sale?: string | null;
  lifecycle_status?: CatalogProductLifecycle;
  metadata?: Record<string, unknown>;
};

const CONTENT_TYPE_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf",
  txt: "text/plain",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  tif: "image/tiff",
  tiff: "image/tiff",
  webp: "image/webp",
};

function parisDate(): string {
  const parts = new Intl.DateTimeFormat("fr-FR", {
    timeZone: "Europe/Paris",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function requestUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (typeof window !== "undefined") {
    return normalizedPath;
  }
  const base = (process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");
  return `${base}${normalizedPath}`;
}

function buildContext(context?: Partial<AuditContext>): AuditContext {
  return {
    as_of_date: context?.as_of_date ?? parisDate(),
    jurisdiction: context?.jurisdiction ?? "FR",
    surface: context?.surface ?? "packaging",
    consumer_facing: context?.consumer_facing ?? true,
    product_identifier: context?.product_identifier ?? "SKU-DEMO-001",
    product_category: context?.product_category ?? "packaging",
    operation_spend_eur: context?.operation_spend_eur ?? null,
  };
}

function buildDossier(hasLcaAttached: boolean, options: AuditOptions): EvidenceDossier {
  const items = [...(options.evidence?.items ?? []), ...(options.additionalEvidence ?? [])];
  const hasLcaMetadata = items.some((item) => item.kind === "lca_report");
  if (hasLcaAttached && !hasLcaMetadata) {
    // The checkbox records the user's declaration of the standard only. Missing report details
    // remain missing, so this cannot activate a Safe Harbor or make an incomplete ACV pass.
    const declaredLca: LcaEvidence = { kind: "lca_report", standard: "ISO 14044" };
    items.push(declaredLca);
  }
  return {
    items,
    legal_person: options.evidence?.legal_person ?? true,
    average_annual_turnover_eur: options.evidence?.average_annual_turnover_eur ?? null,
    advertising_spend_eur: options.evidence?.advertising_spend_eur ?? null,
  };
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `Erreur API (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body) {
        const apiDetail = (body as { detail?: unknown }).detail;
        detail = typeof apiDetail === "string" ? apiDetail : JSON.stringify(apiDetail);
      }
    } catch {
      // Preserve the HTTP status message when the backend does not return JSON.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

async function readResult(response: Response): Promise<RegulatoryAuditResponse> {
  return readJson<RegulatoryAuditResponse>(response);
}

function contentTypeForDocument(file: File): string {
  const extension = file.name.split(".").pop()?.trim().toLowerCase() || "";
  const contentType = CONTENT_TYPE_BY_EXTENSION[extension];
  if (!contentType) {
    throw new Error("Format non pris en charge. Utilisez PDF, TXT, PNG, JPG, TIFF ou WEBP.");
  }
  return contentType;
}

async function sha256File(file: File): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function safeDocumentTitle(file: File, explicitTitle?: string): string {
  const chosen = explicitTitle?.trim();
  if (chosen && chosen.length >= 2) return chosen.slice(0, 500);
  const withoutExtension = file.name.replace(/\.[^.]+$/, "").trim();
  return (withoutExtension.length >= 2 ? withoutExtension : "Document importé").slice(0, 500);
}

async function createJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(requestUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(body),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<T>(response);
}

/**
 * Stores a source document through the quarantine capability flow. The browser
 * receives only a short-lived POST capability; it never chooses an object key.
 */
export async function storeSecureDocument(
  file: File,
  options: SecureDocumentUploadOptions = {},
): Promise<DocumentUploadCompletion> {
  if (file.size <= 0) throw new Error("Le fichier est vide.");
  const contentType = contentTypeForDocument(file);
  const checksum = await sha256File(file);
  const document = await createJson<StoredDocument>("/api/v1/documents", {
    title: safeDocumentTitle(file, options.title),
    document_type: options.documentType ?? "other",
    supplier_id: options.supplierId || null,
    product_id: options.productId || null,
    tags: options.tags ?? ["import"],
  });
  const instruction = await createJson<DocumentUploadInstruction>(`/api/v1/documents/${document.id}/versions`, {
    source_filename: file.name,
    content_type: contentType,
    size_bytes: file.size,
    expected_sha256: checksum,
  });

  // The file part must be appended after all POST policy fields. Rewrap the
  // browser File so its multipart MIME agrees with the signed MIME condition.
  const form = new FormData();
  for (const [name, value] of Object.entries(instruction.upload_fields)) form.append(name, value);
  const normalizedFile = new File([file], file.name, { type: contentType });
  form.append("file", normalizedFile, normalizedFile.name);

  let uploadResponse: Response;
  try {
    uploadResponse = await fetch(instruction.upload_url, {
      method: instruction.upload_method,
      body: form,
      credentials: "omit",
      mode: "cors",
    });
  } catch {
    throw new Error("Le navigateur n’a pas pu joindre le stockage sécurisé. Vérifiez la connexion et réessayez avant l’expiration du lien.");
  }
  if (!uploadResponse.ok) {
    throw new Error("Le stockage sécurisé a refusé le fichier. Aucun document n’a été promu.");
  }

  return createJson<DocumentUploadCompletion>(`/api/v1/document-uploads/${instruction.upload.id}/complete`, {});
}

export async function createSecureDocumentDownloadUrl(versionId: string): Promise<DocumentDownload> {
  return createJson<DocumentDownload>(`/api/v1/document-versions/${versionId}/download-url`, {});
}

export async function getSecureDocument(documentId: string): Promise<StoredDocumentDetail> {
  const response = await fetch(requestUrl(`/api/v1/documents/${documentId}`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<StoredDocumentDetail>(response);
}

export async function getDocumentVersionSegments(versionId: string): Promise<DocumentVersionSegments> {
  const response = await fetch(requestUrl(`/api/v1/document-versions/${versionId}/segments`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<DocumentVersionSegments>(response);
}

export async function retrySecureDocumentExtraction(versionId: string): Promise<DocumentExtractionRetry> {
  return createJson<DocumentExtractionRetry>(`/api/v1/document-versions/${versionId}/extraction/retry`, {});
}

async function catalogMutation<T>(path: string, method: "POST" | "PATCH" | "DELETE", body?: unknown, idempotencyKey?: string): Promise<T> {
  const headers: Record<string, string> = { ...csrfHeaders() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const response = await fetch(requestUrl(path), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "include",
    cache: "no-store",
  });
  if (response.status === 204) return undefined as T;
  return readJson<T>(response);
}

async function listCatalogPages<TItem>(path: string): Promise<TItem[]> {
  const items: TItem[] = [];
  let cursor: string | null = null;
  // The API is cursor-paginated. A bounded UI preload avoids unbounded browser
  // work while still covering normal procurement catalogs without guessing IDs.
  for (let page = 0; page < 5; page += 1) {
    const delimiter = path.includes("?") ? "&" : "?";
    const pageUrl: string = cursor ? `${path}${delimiter}cursor=${encodeURIComponent(cursor)}` : path;
    const response: Response = await fetch(requestUrl(pageUrl), { credentials: "include", cache: "no-store" });
    const payload: { items: TItem[]; next_cursor: string | null } = await readJson(response);
    items.push(...payload.items);
    cursor = payload.next_cursor;
    if (!cursor) break;
  }
  return items;
}

export async function listCatalogSuppliers(): Promise<CatalogSupplier[]> {
  return listCatalogPages<CatalogSupplier>("/api/v1/suppliers?limit=100");
}

export async function listCatalogProducts(supplierId?: string): Promise<CatalogProduct[]> {
  const suffix = supplierId ? `&supplier_id=${encodeURIComponent(supplierId)}` : "";
  return listCatalogPages<CatalogProduct>(`/api/v1/products?limit=100${suffix}`);
}

export async function createCatalogSupplier(input: CatalogSupplierInput, idempotencyKey = createIdempotencyKey("supplier")): Promise<CatalogSupplierCreate> {
  return catalogMutation<CatalogSupplierCreate>("/api/v1/suppliers", "POST", input, idempotencyKey);
}

export async function updateCatalogSupplier(supplierId: string, input: Partial<CatalogSupplierInput>): Promise<CatalogSupplier> {
  return catalogMutation<CatalogSupplier>(`/api/v1/suppliers/${supplierId}`, "PATCH", input);
}

export async function archiveCatalogSupplier(supplierId: string): Promise<void> {
  await catalogMutation<void>(`/api/v1/suppliers/${supplierId}`, "DELETE");
}

export async function createCatalogProduct(input: CatalogProductInput, idempotencyKey = createIdempotencyKey("product")): Promise<CatalogProductCreate> {
  return catalogMutation<CatalogProductCreate>("/api/v1/products", "POST", input, idempotencyKey);
}

export async function updateCatalogProduct(productId: string, input: Partial<Omit<CatalogProductInput, "supplier_id">>): Promise<CatalogProduct> {
  return catalogMutation<CatalogProduct>(`/api/v1/products/${productId}`, "PATCH", input);
}

export async function archiveCatalogProduct(productId: string): Promise<void> {
  await catalogMutation<void>(`/api/v1/products/${productId}`, "DELETE");
}

/** A caller retains this key across a transient retry so queue creation is safe to replay. */
export function createIdempotencyKey(prefix = "analysis"): string {
  if (globalThis.crypto?.randomUUID) return `${prefix}-${globalThis.crypto.randomUUID()}`;
  const random = Math.random().toString(36).slice(2);
  return `${prefix}-${Date.now().toString(36)}-${random}`;
}

async function postPersistentAnalysis<T>(path: string, body: unknown, idempotencyKey: string): Promise<T> {
  const response = await fetch(requestUrl(path), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
      ...csrfHeaders(),
    },
    body: JSON.stringify(body),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<T>(response);
}

/** Queue deterministic citeable-claim detection for an extracted document version. */
export async function createPersistentAnalysis(
  documentVersionId: string,
  idempotencyKey: string,
  context: { supplierId?: string | null; productId?: string | null } = {},
): Promise<PersistentAnalysisEnqueueResponse> {
  return postPersistentAnalysis<PersistentAnalysisEnqueueResponse>(
    "/api/v1/analyses",
    {
      document_version_ids: [documentVersionId],
      supplier_id: context.supplierId || null,
      product_id: context.productId || null,
    },
    idempotencyKey,
  );
}

export async function retryPersistentAnalysis(
  analysisId: string,
  idempotencyKey: string,
): Promise<PersistentAnalysisEnqueueResponse> {
  return postPersistentAnalysis<PersistentAnalysisEnqueueResponse>(
    `/api/v1/analyses/${analysisId}/retry`,
    {},
    idempotencyKey,
  );
}

export async function getPersistentAnalysis(analysisId: string): Promise<PersistentAnalysisDetail> {
  const response = await fetch(requestUrl(`/api/v1/analyses/${analysisId}`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<PersistentAnalysisDetail>(response);
}

export async function getPersistentAnalysisVersion(
  analysisId: string,
  versionNumber: number,
): Promise<PersistentAnalysisVersionDetail> {
  const response = await fetch(requestUrl(`/api/v1/analyses/${analysisId}/versions/${versionNumber}`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<PersistentAnalysisVersionDetail>(response);
}

export async function auditText(
  text: string,
  hasLcaAttached: boolean,
  options: AuditOptions = {},
): Promise<RegulatoryAuditResponse> {
  const payload: EvaluationRequest = {
    source_text: text,
    context: buildContext(options.context),
    evidence: buildDossier(hasLcaAttached, options),
  };
  const response = await fetch(requestUrl("/api/v1/engine/evaluate"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readResult(response);
}

export async function auditFile(
  file: File,
  hasLcaAttached = false,
  options: AuditOptions = {},
): Promise<RegulatoryAuditResponse> {
  const form = new FormData();
  form.append("document", file, file.name);
  form.append("context_json", JSON.stringify(buildContext(options.context)));
  form.append("evidence_json", JSON.stringify(buildDossier(hasLcaAttached, options)));
  const response = await fetch(requestUrl("/api/v1/engine/evaluate"), {
    method: "POST",
    headers: csrfHeaders(),
    body: form,
    credentials: "include",
    cache: "no-store",
  });
  return readResult(response);
}

// ---------------------------------------------------------------------------
// Persistent Evidence Registry and Claim-Evidence Matrix (Chantier 6.2)
// ---------------------------------------------------------------------------

export async function listPersistentEvidence(params: {
  supplierId?: string | null;
  productId?: string | null;
  evidenceType?: PersistentEvidenceType | null;
  status?: PersistentEvidenceStatus | null;
  query?: string | null;
  limit?: number;
  cursor?: string | null;
} = {}): Promise<PersistentEvidenceList> {
  const query = new URLSearchParams();
  if (params.supplierId) query.set("supplier_id", params.supplierId);
  if (params.productId) query.set("product_id", params.productId);
  if (params.evidenceType) query.set("evidence_type", params.evidenceType);
  if (params.status) query.set("status", params.status);
  if (params.query) query.set("query", params.query);
  if (params.limit) query.set("limit", String(params.limit));
  if (params.cursor) query.set("cursor", params.cursor);

  const qs = query.toString();
  const response = await fetch(requestUrl(`/api/v1/evidence${qs ? `?${qs}` : ""}`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<PersistentEvidenceList>(response);
}

export async function getPersistentEvidence(evidenceId: string): Promise<PersistentEvidence> {
  const response = await fetch(requestUrl(`/api/v1/evidence/${evidenceId}`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<PersistentEvidence>(response);
}

export async function createPersistentEvidence(
  payload: PersistentEvidenceCreateRequest,
): Promise<{ evidence: PersistentEvidence; idempotent_replay: boolean }> {
  const response = await fetch(requestUrl("/api/v1/evidence"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<{ evidence: PersistentEvidence; idempotent_replay: boolean }>(response);
}

export async function updatePersistentEvidence(
  evidenceId: string,
  payload: Partial<PersistentEvidenceCreateRequest>,
): Promise<PersistentEvidence> {
  const response = await fetch(requestUrl(`/api/v1/evidence/${evidenceId}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<PersistentEvidence>(response);
}

export async function deletePersistentEvidence(evidenceId: string): Promise<void> {
  const response = await fetch(requestUrl(`/api/v1/evidence/${evidenceId}`), {
    method: "DELETE",
    headers: csrfHeaders(),
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Échec suppression preuve (${response.status})`);
  }
}

export async function linkClaimEvidence(
  claimId: string,
  payload: {
    evidence_id: string;
    relation?: PersistentEvidenceRelation;
    coverage_status?: PersistentEvidenceStatus;
    validity_as_of?: string | null;
    confidence_score?: number | null;
    rationale?: string | null;
  },
): Promise<PersistentEvidenceLink> {
  const response = await fetch(requestUrl(`/api/v1/claims/${claimId}/evidence-links`), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<PersistentEvidenceLink>(response);
}

export async function getClaimEvidenceLinks(claimId: string): Promise<PersistentEvidenceLink[]> {
  const response = await fetch(requestUrl(`/api/v1/claims/${claimId}/evidence-links`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<PersistentEvidenceLink[]>(response);
}

export async function unlinkClaimEvidence(linkId: string): Promise<void> {
  const response = await fetch(requestUrl(`/api/v1/evidence-links/${linkId}`), {
    method: "DELETE",
    headers: csrfHeaders(),
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Échec déliaison preuve (${response.status})`);
  }
}

export async function getAnalysisEvidenceMatrix(
  analysisId: string,
  versionNumber?: number,
): Promise<EvidenceMatrix> {
  const qs = versionNumber !== undefined ? `?version=${versionNumber}` : "";
  const response = await fetch(requestUrl(`/api/v1/analyses/${analysisId}/evidence-matrix${qs}`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<EvidenceMatrix>(response);
}

// ---------------------------------------------------------------------------
// Chantier 6.3 — Human Review Validations & Supplier Evidence Requests
// ---------------------------------------------------------------------------

export async function recordValidation(payload: ValidationCreateRequest): Promise<Validation> {
  const response = await fetch(requestUrl("/api/v1/validations"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<Validation>(response);
}

export async function getVersionValidations(
  analysisId: string,
  versionNumber: number,
): Promise<Validation[]> {
  const response = await fetch(
    requestUrl(`/api/v1/analyses/${analysisId}/versions/${versionNumber}/validations`),
    {
      credentials: "include",
      cache: "no-store",
    },
  );
  return readJson<Validation[]>(response);
}

export async function createEvidenceRequest(
  payload: EvidenceRequestCreateRequest,
): Promise<EvidenceRequest> {
  const response = await fetch(requestUrl("/api/v1/evidence-requests"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<EvidenceRequest>(response);
}

export async function listEvidenceRequests(params?: {
  supplier_id?: string;
  product_id?: string;
  status?: EvidenceRequestStatus;
  limit?: number;
  cursor?: string;
}): Promise<EvidenceRequestList> {
  const searchParams = new URLSearchParams();
  if (params?.supplier_id) searchParams.set("supplier_id", params.supplier_id);
  if (params?.product_id) searchParams.set("product_id", params.product_id);
  if (params?.status) searchParams.set("status", params.status);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.cursor) searchParams.set("cursor", params.cursor);

  const qs = searchParams.toString();
  const url = `/api/v1/evidence-requests${qs ? `?${qs}` : ""}`;
  const response = await fetch(requestUrl(url), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<EvidenceRequestList>(response);
}

export async function getEvidenceRequest(requestId: string): Promise<EvidenceRequest> {
  const response = await fetch(requestUrl(`/api/v1/evidence-requests/${requestId}`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<EvidenceRequest>(response);
}

export async function updateEvidenceRequest(
  requestId: string,
  payload: EvidenceRequestUpdateRequest,
): Promise<EvidenceRequest> {
  const response = await fetch(requestUrl(`/api/v1/evidence-requests/${requestId}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<EvidenceRequest>(response);
}

export async function sendEvidenceRequest(requestId: string): Promise<EvidenceRequest> {
  const response = await fetch(requestUrl(`/api/v1/evidence-requests/${requestId}/send`), {
    method: "POST",
    headers: csrfHeaders(),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<EvidenceRequest>(response);
}

export async function remindEvidenceRequest(requestId: string): Promise<EvidenceRequest> {
  const response = await fetch(requestUrl(`/api/v1/evidence-requests/${requestId}/remind`), {
    method: "POST",
    headers: csrfHeaders(),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<EvidenceRequest>(response);
}

export async function deleteEvidenceRequest(requestId: string): Promise<void> {
  const response = await fetch(requestUrl(`/api/v1/evidence-requests/${requestId}`), {
    method: "DELETE",
    headers: csrfHeaders(),
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Échec suppression demande (${response.status})`);
  }
}

export async function generateEvidenceRequestTemplate(
  payload: TemplateGenerationRequest,
): Promise<TemplateGenerationResponse> {
  const response = await fetch(requestUrl("/api/v1/evidence-requests/generate-template"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<TemplateGenerationResponse>(response);
}

// ---------------------------------------------------------------------------
// Chantier 7 — Regulatory Governance & Rule Book API
// ---------------------------------------------------------------------------

export async function getRuleBookOverview(): Promise<RuleBookSummaryResponse> {
  const response = await fetch(requestUrl("/api/v1/regulatory/rulebook"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<RuleBookSummaryResponse>(response);
}

export async function listRegulatoryRules(filters?: {
  jurisdiction?: Jurisdiction;
  legal_status?: LegalStatus;
  legal_force?: string;
  severity?: string;
  claim_type?: string;
  confidence_level?: ConfidenceLevel;
  search?: string;
}): Promise<RegulatoryRuleSummary[]> {
  const params = new URLSearchParams();
  if (filters?.jurisdiction) params.set("jurisdiction", filters.jurisdiction);
  if (filters?.legal_status) params.set("legal_status", filters.legal_status);
  if (filters?.legal_force) params.set("legal_force", filters.legal_force);
  if (filters?.severity) params.set("severity", filters.severity);
  if (filters?.claim_type) params.set("claim_type", filters.claim_type);
  if (filters?.confidence_level) params.set("confidence_level", filters.confidence_level);
  if (filters?.search) params.set("search", filters.search);

  const qs = params.toString();
  const url = `/api/v1/regulatory/rules${qs ? `?${qs}` : ""}`;
  const response = await fetch(requestUrl(url), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<RegulatoryRuleSummary[]>(response);
}

export async function getRegulatoryRuleDetail(ruleId: string): Promise<RegulatoryRuleDetail> {
  const response = await fetch(requestUrl(`/api/v1/regulatory/rules/${encodeURIComponent(ruleId)}`), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<RegulatoryRuleDetail>(response);
}

export async function getRuleBookChangelog(): Promise<RuleBookChangelogEntry[]> {
  const response = await fetch(requestUrl("/api/v1/regulatory/changelog"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<RuleBookChangelogEntry[]>(response);
}

export async function submitRuleReview(
  ruleId: string,
  payload: RuleReviewSubmissionRequest,
): Promise<RegulatoryRuleDetail> {
  const response = await fetch(requestUrl(`/api/v1/regulatory/rules/${encodeURIComponent(ruleId)}/review`), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<RegulatoryRuleDetail>(response);
}

// ---------------------------------------------------------------------------
// Chantier 8 — B2B Pilot Pack API
// ---------------------------------------------------------------------------

export async function getPilotOverview(): Promise<PilotOverviewResponse> {
  const response = await fetch(requestUrl("/api/v1/pilot/overview"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<PilotOverviewResponse>(response);
}

export async function getPreAuditReport(): Promise<PreAuditReportResponse> {
  const response = await fetch(requestUrl("/api/v1/pilot/pre-audit-report"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<PreAuditReportResponse>(response);
}

export async function importPilotCatalog(
  items: CatalogImportItem[],
): Promise<CatalogImportResult> {
  const response = await fetch(requestUrl("/api/v1/pilot/import-catalog"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify({ items }),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<CatalogImportResult>(response);
}

export async function exportPilotDossier(): Promise<Record<string, unknown>> {
  const response = await fetch(requestUrl("/api/v1/pilot/export-dossier"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<Record<string, unknown>>(response);
}

export async function getRetentionPolicy(): Promise<RetentionPolicyResponse> {
  const response = await fetch(requestUrl("/api/v1/pilot/retention-policy"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<RetentionPolicyResponse>(response);
}

// ---------------------------------------------------------------------------
// Chantier 9 — Enterprise Industrialization API
// ---------------------------------------------------------------------------

export async function createApiKey(
  payload: ApiKeyCreateRequest,
): Promise<ApiKeyCreatedResponse> {
  const response = await fetch(requestUrl("/api/v1/enterprise/api-keys"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<ApiKeyCreatedResponse>(response);
}

export async function listApiKeys(): Promise<ApiKeySummary[]> {
  const response = await fetch(requestUrl("/api/v1/enterprise/api-keys"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<ApiKeySummary[]>(response);
}

export async function revokeApiKey(keyId: string): Promise<void> {
  const response = await fetch(requestUrl(`/api/v1/enterprise/api-keys/${encodeURIComponent(keyId)}`), {
    method: "DELETE",
    headers: csrfHeaders(),
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Échec de révocation de la clé (${response.status})`);
  }
}

export async function getEnterpriseMetrics(): Promise<EnterpriseMetricsResponse> {
  const response = await fetch(requestUrl("/api/v1/enterprise/metrics"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<EnterpriseMetricsResponse>(response);
}

export async function getEnterpriseAlerts(): Promise<EnterpriseAlert[]> {
  const response = await fetch(requestUrl("/api/v1/enterprise/alerts"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<EnterpriseAlert[]>(response);
}

export async function createLegalHold(
  payload: LegalHoldRequest,
): Promise<LegalHoldResponse> {
  const response = await fetch(requestUrl("/api/v1/enterprise/legal-holds"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(payload),
    credentials: "include",
    cache: "no-store",
  });
  return readJson<LegalHoldResponse>(response);
}

export async function listLegalHolds(): Promise<LegalHoldResponse[]> {
  const response = await fetch(requestUrl("/api/v1/enterprise/legal-holds"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<LegalHoldResponse[]>(response);
}

// ---------------------------------------------------------------------------
// Chantier 10 — Regulatory PDF Reporting & Opposable Dossier
// ---------------------------------------------------------------------------

export async function downloadPdfReport(
  report: RegulatoryAuditResponse,
  options?: PdfExportOptions,
): Promise<void> {
  const response = await fetch(requestUrl("/api/v1/reports/pdf"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify({
      evaluation_response: report,
      document_title: options?.document_title || "Rapport d'Audit Pré-Réglementaire Allégations",
      product_identifier: options?.product_identifier || "SKU-PROD-001",
      surface: options?.surface || "packaging",
      include_evidence_matrix: options?.include_evidence_matrix ?? true,
      include_remediation_clauses: options?.include_remediation_clauses ?? true,
    }),
    credentials: "include",
  });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Échec de génération du PDF (${response.status})`);
  }
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `rapport-pre-audit-${new Date().toISOString().slice(0, 10)}.pdf`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}

export async function downloadDossierPack(
  report: RegulatoryAuditResponse,
  options?: PdfExportOptions,
): Promise<void> {
  const response = await fetch(requestUrl("/api/v1/reports/dossier"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify({
      evaluation_response: report,
      document_title: options?.document_title || "Dossier Probatoire Réglementaire",
      product_identifier: options?.product_identifier || "SKU-PROD-001",
      surface: options?.surface || "packaging",
    }),
    credentials: "include",
  });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Échec de génération du dossier ZIP (${response.status})`);
  }
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `dossier-probatoire-${new Date().toISOString().slice(0, 10)}.zip`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Chantier 11 — Tamper-Evident Audit Chain & Ledger Integrity API
// ---------------------------------------------------------------------------

export async function listAuditEvents(params?: {
  entity_type?: string;
  action?: string;
  limit?: number;
}): Promise<AuditEventLog[]> {
  const q = new URLSearchParams();
  if (params?.entity_type) q.set("entity_type", params.entity_type);
  if (params?.action) q.set("action", params.action);
  if (params?.limit) q.set("limit", String(params.limit));

  const endpoint = `/api/v1/audit/events${q.toString() ? `?${q.toString()}` : ""}`;
  const response = await fetch(requestUrl(endpoint), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<AuditEventLog[]>(response);
}

export async function verifyAuditChain(): Promise<AuditChainVerification> {
  const response = await fetch(requestUrl("/api/v1/audit/verify"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<AuditChainVerification>(response);
}

export async function getAuditIntegrityCertificate(): Promise<AuditIntegrityCertificate> {
  const response = await fetch(requestUrl("/api/v1/audit/integrity-certificate"), {
    credentials: "include",
    cache: "no-store",
  });
  return readJson<AuditIntegrityCertificate>(response);
}
