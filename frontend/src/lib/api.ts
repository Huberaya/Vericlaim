import type {
  AuditContext,
  AuditHistoryResponse,
  EvidenceDossier,
  EvidenceItem,
  EvaluationRequest,
  LcaEvidence,
  RegulatoryAuditResponse,
  SupplierCompareRequest,
  SupplierCompareResponse,
} from "@/lib/types";

export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

export type AuditOptions = {
  context?: Partial<AuditContext>;
  evidence?: Partial<EvidenceDossier>;
  additionalEvidence?: EvidenceItem[];
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

function requestUrl(path: string): string {
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

async function readResult(response: Response): Promise<RegulatoryAuditResponse> {
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
  return (await response.json()) as RegulatoryAuditResponse;
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
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
    body: form,
    cache: "no-store",
  });
  return readResult(response);
}

export async function downloadAuditPdf(report: RegulatoryAuditResponse): Promise<void> {
  const response = await fetch(requestUrl("/api/v1/engine/export/pdf"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(report),
  });
  if (!response.ok) {
    let msg = `Erreur lors de la génération du PDF (${response.status})`;
    try {
      const err = await response.json();
      if (err?.detail) msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    } catch {
      // Ignorer
    }
    throw new Error(msg);
  }
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const shortId = (report.audit_trail?.audit_id || "rapport").replace(/-/g, "").slice(0, 8).toUpperCase();
  a.download = `VeriClaim_Attestation_${shortId}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

export async function fetchAuditHistory(params?: {
  limit?: number;
  offset?: number;
  supplier?: string;
  compliance?: string;
  search?: string;
}): Promise<AuditHistoryResponse> {
  const query = new URLSearchParams();
  if (params?.limit) query.set("limit", String(params.limit));
  if (params?.offset) query.set("offset", String(params.offset));
  if (params?.supplier) query.set("supplier", params.supplier);
  if (params?.compliance) query.set("compliance", params.compliance);
  if (params?.search) query.set("search", params.search);
  const qs = query.toString();
  const url = requestUrl(`/api/v1/engine/audits${qs ? `?${qs}` : ""}`);
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Échec du chargement de l'historique (${response.status})`);
  }
  return (await response.json()) as AuditHistoryResponse;
}

export async function fetchAuditById(auditId: string): Promise<RegulatoryAuditResponse> {
  const response = await fetch(requestUrl(`/api/v1/engine/audits/${encodeURIComponent(auditId)}`), {
    cache: "no-store",
  });
  return readResult(response);
}

export async function compareSuppliers(
  payload: SupplierCompareRequest,
): Promise<SupplierCompareResponse> {
  const response = await fetch(requestUrl("/api/v1/engine/suppliers/compare"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = `Erreur API (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new Error(detail);
  }
  return (await response.json()) as SupplierCompareResponse;
}
