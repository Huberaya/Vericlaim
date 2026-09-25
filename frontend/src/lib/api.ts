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
  PersistentAnalysisDetail,
  PersistentAnalysisEnqueueResponse,
  PersistentAnalysisVersionDetail,
  StoredDocumentDetail,
  EvidenceDossier,
  EvidenceItem,
  EvaluationRequest,
  LcaEvidence,
  RegulatoryAuditResponse,
  StoredDocument,
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
  if (typeof window !== "undefined") {
    const host = window.location.hostname.toLowerCase();
    const isLocalHost = host === "localhost" || host === "127.0.0.1" || host === "::1";
    // The Next rewrite proxies preview/non-local requests, avoiding browser calls to localhost.
    if (!isLocalHost) return path;
  }
  return `${API_BASE_URL}${path}`;
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
