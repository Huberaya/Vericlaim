"use client";

import { useState } from "react";
import CatalogPanel from "@/components/CatalogPanel";
import ClaimHighlighter from "@/components/ClaimHighlighter";
import DocumentUploader from "@/components/DocumentUploader";
import LegalScoreCard from "@/components/LegalScoreCard";
import ProofUploadModal from "@/components/ProofUploadModal";
import RemediationModal from "@/components/RemediationModal";
import SecureDocumentVault from "@/components/SecureDocumentVault";
import { auditFile, auditText } from "@/lib/api";
import type {
  AuditContext,
  AuthSession,
  ClaimEvaluation,
  EvidenceItem,
  OrganizationMembership,
  RegulatoryAuditResponse,
  Surface,
} from "@/lib/types";

const SURFACES: { value: Surface; label: string }[] = [
  { value: "packaging", label: "Emballage / packaging" },
  { value: "product_label", label: "Étiquette produit" },
  { value: "advertisement", label: "Publicité" },
  { value: "online_store", label: "Fiche e-commerce" },
  { value: "supplier_contract", label: "Contrat fournisseur" },
  { value: "unknown", label: "À préciser" },
];

const OVERALL_LABEL: Record<RegulatoryAuditResponse["overall_compliance"], string> = {
  COMPLIANT: "Conforme au périmètre contrôlé",
  NON_COMPLIANT: "Non-conformité juridique retenue",
  CONDITIONAL_REJECT: "Publication à suspendre · preuves manquantes",
  REVIEW_REQUIRED: "Revue juridique / probatoire requise",
  UPCOMING_REQUIREMENTS: "Exigences à venir · anticiper",
  NO_CLAIMS_DETECTED: "Aucune allégation détectée par le lexique",
};

function parisToday(): string {
  const parts = new Intl.DateTimeFormat("fr-FR", {
    timeZone: "Europe/Paris",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function badgeClass(status: RegulatoryAuditResponse["overall_compliance"]): string {
  if (status === "NON_COMPLIANT") return "result-status result-status-critical";
  if (status === "COMPLIANT" || status === "NO_CLAIMS_DETECTED") return "result-status result-status-positive";
  return "result-status result-status-warning";
}

function evidenceLabel(item: EvidenceItem): string {
  switch (item.kind) {
    case "lca_report": return `ACV ${item.standard || "déclarée"}${item.reference ? ` · ${item.reference}` : ""}`;
    case "ecolabel_certificate": return `Écolabel · ${item.license_number}`;
    case "recycling_route": return `Filière · ${item.material_or_component || "composant non précisé"}`;
    case "ghg_inventory": return "Bilan GES produit";
    case "ghg_reduction_plan": return "Trajectoire de réduction";
    case "carbon_offset": return `Compensation · ${item.standard_or_registry || "registre non précisé"}`;
    case "other": return item.description || "Autre preuve déclarée";
  }
}

type Props = {
  auth: AuthSession;
  activeMembership: OrganizationMembership;
  isSessionActionLoading: boolean;
  onLogout: () => void;
  onSwitchOrganization: (organizationId: string) => void;
};

function userInitials(label: string): string {
  return label
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "VC";
}

export default function AuditDashboard({
  auth,
  activeMembership,
  isSessionActionLoading,
  onLogout,
  onSwitchOrganization,
}: Props) {
  const [surface, setSurface] = useState<Surface>("packaging");
  const [auditDate, setAuditDate] = useState(parisToday);
  const [consumerFacing, setConsumerFacing] = useState(true);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [report, setReport] = useState<RegulatoryAuditResponse | null>(null);
  const [sourceText, setSourceText] = useState("");
  const [selectedAssessments, setSelectedAssessments] = useState<ClaimEvaluation[]>([]);
  const [proofModalOpen, setProofModalOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catalogRevision, setCatalogRevision] = useState(0);

  function buildContext(): AuditContext {
    return {
      as_of_date: auditDate,
      jurisdiction: "FR",
      surface,
      consumer_facing: consumerFacing,
      product_identifier: "SKU-DEMO-001",
      product_category: "packaging",
    };
  }

  async function runTextAudit(text: string, hasLcaAttached: boolean, additionalEvidence: EvidenceItem[] = []) {
    setSourceText(text);
    setReport(null);
    setError(null);
    setIsLoading(true);
    try {
      const result = await auditText(text, hasLcaAttached, {
        context: buildContext(),
        evidence: { items: evidence, legal_person: true },
        additionalEvidence,
      });
      setSourceText(result.extracted_source_text);
      setReport(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "L’API n’a pas pu analyser le texte.");
    } finally {
      setIsLoading(false);
    }
  }

  async function runFileAudit(file: File, hasLcaAttached: boolean) {
    setReport(null);
    setSourceText("");
    setError(null);
    setIsLoading(true);
    try {
      const result = await auditFile(file, hasLcaAttached, {
        context: buildContext(),
        evidence: { items: evidence, legal_person: true },
      });
      setSourceText(result.extracted_source_text);
      setReport(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Le document n’a pas pu être extrait ou analysé.");
    } finally {
      setIsLoading(false);
    }
  }

  function clearReportForContextChange(next: () => void) {
    next();
    setReport(null);
    setError(null);
  }

  const activeRuleCount = report ? new Set(report.evaluations.map((item) => item.rule_id)).size : 0;

  return (
    <div className="app-shell min-h-screen">
      <aside className="sidebar flex flex-col">
        <a className="brand" href="#main-content" aria-label="VeriClaim AI, accueil">
          <span className="brand-symbol">V</span>
          <span className="brand-wordmark">VeriClaim<span> AI</span><small>REGULATORY INTELLIGENCE</small></span>
        </a>
        <div className="sidebar-section-label">ESPACE DE TRAVAIL</div>
        <nav className="sidebar-nav" aria-label="Navigation principale">
          <a className="sidebar-link sidebar-link-active" href="#audit"><span className="nav-icon">◈</span> Audit des allégations<span className="nav-indicator" /></a>
          <a className="sidebar-link" href="#catalog"><span className="nav-icon">◫</span> Catalogue achats</a>
          <a className="sidebar-link" href="#documents"><span className="nav-icon">▣</span> Pièces fournisseurs</a>
          <a className="sidebar-link" href="#results-title"><span className="nav-icon">⌘</span> Rapport d’analyse<span className="nav-count">08</span></a>
        </nav>
        <div className="sidebar-rule-card">
          <div className="rule-card-icon">✓</div>
          <strong>Règles versionnées</strong>
          <p>AGEC · EmpCo 2024/825 · ISO · contrôles internes</p>
          <span>JURIDICTION FR / UE</span>
        </div>
        <div className="sidebar-bottom">
          <span className="sidebar-status-dot" /> Session SSO sécurisée
          <small>{activeMembership.organization.slug} · Règle de transposition UE : état serveur configurable</small>
        </div>
      </aside>

      <main className="main-area" id="main-content">
        <header className="topbar flex items-center justify-between">
          <div className="breadcrumb"><span>{activeMembership.organization.name}</span><b>/</b><strong>Audit environnemental</strong></div>
          <div className="topbar-meta">
            <label className="workspace-switcher">
              <span className="visually-hidden">Organisation active</span>
              <select
                value={activeMembership.organization.id}
                disabled={isLoading || isSessionActionLoading}
                onChange={(event) => onSwitchOrganization(event.target.value)}
              >
                {auth.memberships.map((membership) => (
                  <option key={membership.id} value={membership.organization.id}>
                    {membership.organization.name}
                  </option>
                ))}
              </select>
            </label>
            <span className="secure-label"><span>●</span> {activeMembership.role.name.toUpperCase()}</span>
            <span className="avatar" title={auth.user.email}>{userInitials(auth.user.display_name || auth.user.email)}</span>
            <button type="button" className="topbar-logout" onClick={onLogout} disabled={isSessionActionLoading}>Déconnexion</button>
          </div>
        </header>

        <div className="page-content" id="audit">
          <section className="page-hero">
            <div>
              <div className="page-kicker"><span className="kicker-line" /> AUDIT RÉGLEMENTAIRE · FRANCE / UNION EUROPÉENNE</div>
              <h1>La preuve avant<br className="hero-break" /> la promesse.</h1>
              <p>Évaluez vos allégations environnementales à l’aide d’un moteur déterministe, de règles sourcées et d’un parcours de preuve explicable.</p>
            </div>
            <div className="hero-meta-card">
              <span className="hero-meta-icon">◷</span>
              <span><small>DATE D’ÉVALUATION</small><strong>{new Intl.DateTimeFormat("fr-FR", { dateStyle: "long", timeZone: "Europe/Paris" }).format(new Date(`${auditDate}T12:00:00+02:00`))}</strong></span>
            </div>
          </section>

          <section className="context-bar" aria-label="Contexte de l’audit">
            <label className="context-field">
              <span>SUPPORT</span>
              <select value={surface} disabled={isLoading} onChange={(event) => clearReportForContextChange(() => setSurface(event.target.value as Surface))}>
                {SURFACES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label className="context-field context-date">
              <span>DATE D’ANALYSE</span>
              <input type="date" value={auditDate} disabled={isLoading} onChange={(event) => { if (event.target.value) clearReportForContextChange(() => setAuditDate(event.target.value)); }} />
            </label>
            <label className="context-checkbox">
              <input type="checkbox" checked={consumerFacing} disabled={isLoading} onChange={(event) => clearReportForContextChange(() => setConsumerFacing(event.target.checked))} />
              <span className="checkbox-mark" aria-hidden="true" />
              <span>Communication consommateur</span>
            </label>
            <span className="context-jurisdiction"><span className="jurisdiction-dot" /> JURIDICTION FR</span>
          </section>

          <div className="dashboard-grid grid">
            <DocumentUploader
              isLoading={isLoading}
              error={error}
              onAuditText={runTextAudit}
              onAuditFile={runFileAudit}
              onOpenEvidence={() => setProofModalOpen(true)}
            />
            <LegalScoreCard report={report} />
          </div>

          <CatalogPanel
            key={`catalog-${activeMembership.organization.id}`}
            canRead={activeMembership.role.permissions.includes("catalog:read")}
            canManage={activeMembership.role.permissions.includes("catalog:manage")}
            onCatalogChanged={() => setCatalogRevision((current) => current + 1)}
          />

          <SecureDocumentVault
            key={`vault-${activeMembership.organization.id}`}
            canManage={activeMembership.role.permissions.includes("documents:manage")}
            canRunAnalysis={activeMembership.role.permissions.includes("audit:run")}
            canReadCatalog={activeMembership.role.permissions.includes("catalog:read")}
            catalogRevision={catalogRevision}
          />

          <section className="surface-card results-card" aria-labelledby="results-title">
            <div className="results-header">
              <div>
                <div className="section-eyebrow"><span className="step-chip">03</span> Rapport d’analyse</div>
                <h2 className="card-title" id="results-title">Allégations détectées & décisions du Rule Book</h2>
                <p className="card-description">Cliquez sur une zone surlignée ou une règle pour ouvrir l’explication et la clause de remédiation.</p>
              </div>
              {report && <span className={badgeClass(report.overall_compliance)}>{OVERALL_LABEL[report.overall_compliance]}</span>}
            </div>

            {!report ? (
              <div className="results-empty">
                <div className="empty-document-icon" aria-hidden="true">⌁</div>
                <strong>Le rapport détaillé s’affichera ici</strong>
                <p>Choisissez l’un des deux exemples de démonstration pour lancer l’analyse sans fichier externe.</p>
              </div>
            ) : (
              <div className="results-body">
                <div className="report-toolbar">
                  <div className="report-toolbar-label"><span className="report-live-dot" /> TEXTE EXTRAIT / ANALYSÉ</div>
                  <span>{report.detected_claims_count} allégation(s) · {activeRuleCount} règle(s) mobilisée(s)</span>
                </div>
                <ClaimHighlighter
                  sourceText={sourceText || report.extracted_source_text}
                  evaluations={report.evaluations}
                  onSelect={setSelectedAssessments}
                />
                <div className="highlight-legend">
                  <span><i className="legend-swatch legend-critical" />Interdiction / violation retenue</span>
                  <span><i className="legend-swatch legend-warning" />Preuve ou revue requise</span>
                  <span><i className="legend-swatch legend-positive" />Conforme au périmètre</span>
                </div>

                <div className="evaluation-list">
                  {report.evaluations.length === 0 ? (
                    <div className="no-claims-note"><strong>Aucune règle du lexique ne correspond.</strong><span>L’absence de détection ne garantit pas l’absence d’allégation trompeuse. Vérifiez manuellement le document complet.</span></div>
                  ) : report.evaluations.map((item, index) => (
                    <button
                      type="button"
                      className="evaluation-row"
                      key={`${item.claim_id}-${item.rule_id}-${index}`}
                      onClick={() => setSelectedAssessments(report.evaluations.filter((candidate) => candidate.claim_id === item.claim_id))}
                    >
                      <span className={`evaluation-severity evaluation-severity-${item.is_legal_violation ? "critical" : item.verdict === "COMPLIANT" ? "positive" : "warning"}`} aria-hidden="true" />
                      <span className="evaluation-row-main">
                        <strong>{item.rule_title}</strong>
                        <small>« {item.claim_text} » · {item.rule_id}</small>
                      </span>
                      <span className="evaluation-row-status">{item.verdict.replaceAll("_", " ")}</span>
                      <span className="evaluation-row-arrow" aria-hidden="true">↗</span>
                    </button>
                  ))}
                </div>

                <div className="audit-manifest">
                  <div><small>PISTE D’AUDIT</small><strong>{report.audit_trail.audit_id.slice(0, 14)}</strong></div>
                  <div><small>RULE BOOK</small><strong>{report.audit_trail.rulebook_version}</strong></div>
                  <div><small>RAPPORT SHA-256</small><strong title={report.audit_trail.report_sha256}>{report.audit_trail.report_sha256.slice(0, 20)}…</strong></div>
                  <div><small>EXTRACTION</small><strong>{report.audit_trail.extraction_method.replaceAll("_", " ")}</strong></div>
                </div>
              </div>
            )}
          </section>

          <footer className="page-disclaimer">
            <span className="disclaimer-icon" aria-hidden="true">i</span>
            <p><strong>Limite juridique.</strong> Le résultat dépend du texte fourni, des métadonnées déclarées, de la juridiction et de la date. Le statut français de transposition de la directive 2024/825 est configurable côté serveur et doit être confirmé avant toute conclusion opposable. Ce rapport n’est ni un avis juridique, ni une certification, ni un constat de la DGCCRF.</p>
            <span className="footer-version">VERICLAIM AI · 0.1.0</span>
          </footer>
        </div>
      </main>

      <ProofUploadModal
        isOpen={proofModalOpen}
        onClose={() => setProofModalOpen(false)}
        onAdd={(item) => {
          setEvidence((current) => [...current, item]);
          setReport(null);
          setError(null);
        }}
      />
      {selectedAssessments.length > 0 && (
        <RemediationModal evaluations={selectedAssessments} onClose={() => setSelectedAssessments([])} />
      )}
    </div>
  );
}
