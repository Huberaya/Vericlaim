"use client";

import { useEffect, useState } from "react";
import { compareSuppliers, downloadAuditPdf, fetchAuditById, fetchAuditHistory } from "@/lib/api";
import type {
  AuditHistoryItem,
  OverallCompliance,
  RegulatoryAuditResponse,
  SupplierCompareResponse,
  SupplierSubmission,
} from "@/lib/types";

type Props = {
  onLoadAuditReport: (report: RegulatoryAuditResponse) => void;
  onOpenNewAudit: () => void;
};

const DEMO_SUPPLIERS: SupplierSubmission[] = [
  {
    supplier_name: "Fournisseur A (EcoPack Solutions)",
    product_identifier: "SKU-ECO-A1",
    source_text:
      "Emballage certifié Ecolabel Européen (licence FR/012/345). Réduction de 25% de CO2 documentée.",
  },
  {
    supplier_name: "Fournisseur B (BioGreen Pack)",
    product_identifier: "SKU-BIO-B2",
    source_text:
      "Barquette 100% compostable à domicile certifiée NF T 51-800, comporte au moins 40% de matières recyclées.",
  },
  {
    supplier_name: "Fournisseur C (PureNature Packaging)",
    product_identifier: "SKU-RISK-C3",
    source_text:
      "Flacon 100% biodégradable, zéro déchet, formule 100% sans produits chimiques et neutre en carbone par compensation.",
  },
];

const COMPLIANCE_BADGES: Record<OverallCompliance, { label: string; color: string }> = {
  COMPLIANT: { label: "CONFORME", color: "badge-green" },
  REVIEW_REQUIRED: { label: "PREUVES REQUISES", color: "badge-amber" },
  CONDITIONAL_REJECT: { label: "SUSPENDU", color: "badge-amber" },
  UPCOMING_REQUIREMENTS: { label: "À PRÉPARER (UE)", color: "badge-amber" },
  NON_COMPLIANT: { label: "NON CONFORME", color: "badge-red" },
  NO_CLAIMS_DETECTED: { label: "AUCUNE CLAIM", color: "badge-gray" },
};

function formatEur(amount: number | string | null | undefined): string {
  if (amount === null || amount === undefined) return "Non chiffré";
  const num = Number(amount);
  if (!Number.isFinite(num)) return "Non chiffré";
  return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(num);
}

export default function SupplierBenchmarkView({ onLoadAuditReport, onOpenNewAudit }: Props) {
  const [activeTab, setActiveTab] = useState<"benchmark" | "history">("benchmark");
  const [historyItems, setHistoryItems] = useState<AuditHistoryItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selectedAuditIds, setSelectedAuditIds] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [complianceFilter, setComplianceFilter] = useState("");

  const [comparisonResult, setComparisonResult] = useState<SupplierCompareResponse | null>(null);
  const [isComparing, setIsComparing] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [copiedClauseIndex, setCopiedClauseIndex] = useState<number | null>(null);
  const [loadingAuditId, setLoadingAuditId] = useState<string | null>(null);

  // Charger l'historique
  const loadHistory = async () => {
    setIsLoadingHistory(true);
    setHistoryError(null);
    try {
      const resp = await fetchAuditHistory({
        limit: 50,
        search: searchQuery || undefined,
        compliance: complianceFilter || undefined,
      });
      setHistoryItems(resp.items);
      setHistoryTotal(resp.total);
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : "Erreur de chargement de l'historique");
    } finally {
      setIsLoadingHistory(false);
    }
  };

  useEffect(() => {
    if (activeTab === "history") {
      void loadHistory();
    }
  }, [activeTab, complianceFilter]);

  // Lancer le benchmark de démonstration
  const runDemoBenchmark = async () => {
    setIsComparing(true);
    setCompareError(null);
    try {
      const resp = await compareSuppliers({ submissions: DEMO_SUPPLIERS });
      setComparisonResult(resp);
      setActiveTab("benchmark");
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : "Erreur lors du benchmark fournisseur");
    } finally {
      setIsComparing(false);
    }
  };

  // Comparer les audits sélectionnés dans l'historique
  const compareSelectedFromHistory = async () => {
    if (selectedAuditIds.length < 2) {
      alert("Sélectionnez au moins 2 audits dans l'historique pour lancer le comparatif.");
      return;
    }
    setIsComparing(true);
    setCompareError(null);
    try {
      const resp = await compareSuppliers({ audit_ids: selectedAuditIds });
      setComparisonResult(resp);
      setActiveTab("benchmark");
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : "Erreur lors du benchmark");
    } finally {
      setIsComparing(false);
    }
  };

  // Charger un audit précis dans l'analyseur
  const handleLoadAudit = async (auditId: string) => {
    setLoadingAuditId(auditId);
    try {
      const report = await fetchAuditById(auditId);
      onLoadAuditReport(report);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Impossible de charger cet audit");
    } finally {
      setLoadingAuditId(null);
    }
  };

  // Télécharger le PDF d'un audit archivé
  const handleDownloadArchivedPdf = async (auditId: string) => {
    try {
      const report = await fetchAuditById(auditId);
      await downloadAuditPdf(report);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Erreur lors du téléchargement PDF");
    }
  };

  const copyClause = (clause: string, index: number) => {
    navigator.clipboard.writeText(clause);
    setCopiedClauseIndex(index);
    setTimeout(() => setCopiedClauseIndex(null), 2500);
  };

  const toggleSelectAudit = (id: string) => {
    setSelectedAuditIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  return (
    <div className="benchmark-container">
      {/* Barre d'onglets de vue */}
      <div className="benchmark-subnav flex items-center justify-between">
        <div className="subnav-tabs flex gap-2">
          <button
            type="button"
            className={`subnav-tab ${activeTab === "benchmark" ? "subnav-tab-active" : ""}`}
            onClick={() => setActiveTab("benchmark")}
          >
            <span>⚖</span> Comparateur & Benchmark Achats
          </button>
          <button
            type="button"
            className={`subnav-tab ${activeTab === "history" ? "subnav-tab-active" : ""}`}
            onClick={() => {
              setActiveTab("history");
              void loadHistory();
            }}
          >
            <span>⏱</span> Registre & Historique des Audits ({historyTotal})
          </button>
        </div>

        <div className="subnav-actions flex gap-2 items-center">
          {activeTab === "benchmark" && !comparisonResult && (
            <button
              type="button"
              className="button button-primary"
              onClick={runDemoBenchmark}
              disabled={isComparing}
            >
              <span>{isComparing ? "⏳" : "✦"}</span>
              <span>Lancer le Comparatif Démo (3 Fournisseurs)</span>
            </button>
          )}
          {activeTab === "benchmark" && comparisonResult && (
            <button
              type="button"
              className="button button-secondary"
              onClick={runDemoBenchmark}
              disabled={isComparing}
            >
              <span>↻</span> Relancer le Démo
            </button>
          )}
          <button type="button" className="button button-quiet" onClick={onOpenNewAudit}>
            <span>＋</span> Nouvel audit direct
          </button>
        </div>
      </div>

      {compareError && <div className="benchmark-alert alert-error">{compareError}</div>}

      {/* VUE 1 : COMPARATEUR FOURNISSEURS */}
      {activeTab === "benchmark" && (
        <div className="benchmark-content">
          {!comparisonResult ? (
            <div className="surface-card benchmark-empty-state">
              <div className="empty-shield" aria-hidden="true">⚖</div>
              <h3 className="card-title">Benchmark Fournisseurs & Sécurisation Achats</h3>
              <p className="card-description" style={{ maxWidth: "600px", margin: "10px auto" }}>
                Comparez les allégations environnementales, la solidité probatoire (ACV, écolabels) et l’exposition financière de plusieurs fournisseurs pour vos appels d’offres packaging et produits.
              </p>
              <div style={{ display: "flex", gap: "12px", justifyContent: "center", marginTop: "16px" }}>
                <button
                  type="button"
                  className="button button-primary"
                  onClick={runDemoBenchmark}
                  disabled={isComparing}
                  style={{ padding: "10px 18px", fontSize: "12px" }}
                >
                  {isComparing ? "Analyse comparative en cours…" : "Lancer le Comparatif Démo (3 Fournisseurs Packaging)"}
                </button>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => {
                    setActiveTab("history");
                    void loadHistory();
                  }}
                  style={{ padding: "10px 18px", fontSize: "12px" }}
                >
                  Choisir parmi les audits passés
                </button>
              </div>
            </div>
          ) : (
            <div>
              {/* Synthèse du benchmark */}
              <div className="surface-card benchmark-summary-banner">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="section-eyebrow">DÉCISION D'ARBITRAGE ACHAT</div>
                    <h2 className="card-title">Classement et Recommandations Réglementaires</h2>
                    <p className="card-description" style={{ marginTop: "4px" }}>
                      {comparisonResult.benchmark_summary}
                    </p>
                  </div>
                  {comparisonResult.best_supplier && (
                    <div className="best-supplier-badge">
                      <small>FOURNISSEUR RECOMMANDÉ</small>
                      <strong>{comparisonResult.best_supplier}</strong>
                    </div>
                  )}
                </div>
              </div>

              {/* Cartes de comparaison côte-à-côte */}
              <div className="supplier-cards-grid">
                {comparisonResult.ranked_suppliers.map((supp, idx) => {
                  const badge = COMPLIANCE_BADGES[supp.overall_compliance] || {
                    label: supp.overall_compliance,
                    color: "badge-gray",
                  };
                  return (
                    <div
                      key={supp.supplier_name}
                      className={`surface-card supplier-card supplier-rank-${supp.rank}`}
                    >
                      <div className="supplier-card-header flex items-center justify-between">
                        <div className="rank-indicator">
                          <span className="rank-num">#{supp.rank}</span>
                          <span className="rank-text">
                            {supp.rank === 1 ? "1er choix" : `Rang ${supp.rank}`}
                          </span>
                        </div>
                        <span className={`status-pill ${badge.color}`}>
                          {badge.label}
                        </span>
                      </div>

                      <div className="supplier-meta">
                        <h3 className="supplier-title">{supp.supplier_name}</h3>
                        <div className="supplier-sku">SKU : {supp.product_identifier || "N/A"}</div>
                      </div>

                      <div className="supplier-score-row flex items-center justify-between">
                        <div>
                          <div className="score-label">Indice de risque légal</div>
                          <div className="score-val">
                            <strong>{supp.risk_score}</strong>
                            <small>/100</small>
                          </div>
                        </div>
                        <div>
                          <div className="score-label">Exposition financière max</div>
                          <div className="fine-val">
                            {formatEur(supp.max_known_fine_eur)}
                          </div>
                        </div>
                      </div>

                      <div className={`recommendation-box rec-box-${supp.recommendation_color}`}>
                        <div className="rec-title">Décision conformité</div>
                        <p>{supp.recommendation}</p>
                      </div>

                      <div className="supplier-claims-box">
                        <div className="box-subtitle">Allégations détectées</div>
                        {supp.claims_detected.length > 0 ? (
                          <div className="claims-tags flex flex-wrap gap-1">
                            {supp.claims_detected.map((cl, cIdx) => (
                              <span key={cIdx} className="claim-tag">« {cl} »</span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-muted text-xs">Aucune allégation spécifique</span>
                        )}
                      </div>

                      <div className="supplier-violations-box">
                        <div className="box-subtitle">
                          {supp.violations_count === 0 ? "✓ 0 Infraction retenue" : `⚠ ${supp.violations_count} Infraction(s) retenue(s)`}
                        </div>
                        {supp.violations_summary.length > 0 && (
                          <ul className="violations-list">
                            {supp.violations_summary.map((v, vIdx) => (
                              <li key={vIdx}>{v}</li>
                            ))}
                          </ul>
                        )}
                      </div>

                      <div className="supplier-clause-box">
                        <div className="flex items-center justify-between" style={{ marginBottom: "6px" }}>
                          <span className="box-subtitle">Clause contractuelle suggérée</span>
                          <button
                            type="button"
                            className="button button-quiet"
                            style={{ fontSize: "10px", padding: "3px 8px" }}
                            onClick={() => copyClause(supp.procurement_clause, idx)}
                          >
                            {copiedClauseIndex === idx ? "✓ Copié" : "Copier clause"}
                          </button>
                        </div>
                        <p className="clause-text">{supp.procurement_clause}</p>
                      </div>

                      {supp.audit_id && (
                        <div className="supplier-card-actions flex gap-2" style={{ marginTop: "14px" }}>
                          <button
                            type="button"
                            className="button button-primary"
                            style={{ flex: 1, fontSize: "11px" }}
                            onClick={() => handleLoadAudit(supp.audit_id!)}
                            disabled={loadingAuditId === supp.audit_id}
                          >
                            {loadingAuditId === supp.audit_id ? "Chargement…" : "Ouvrir l'audit complet"}
                          </button>
                          <button
                            type="button"
                            className="button button-secondary"
                            style={{ fontSize: "11px" }}
                            onClick={() => handleDownloadArchivedPdf(supp.audit_id!)}
                          >
                            PDF
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Matrice comparative des critères */}
              <div className="surface-card matrix-card" style={{ marginTop: "24px" }}>
                <h3 className="card-title">Matrice Comparative Déterministe</h3>
                <div className="matrix-table-wrap" style={{ overflowX: "auto", marginTop: "12px" }}>
                  <table className="matrix-table w-full">
                    <thead>
                      <tr>
                        <th style={{ textAlign: "left" }}>Critère d'évaluation</th>
                        {comparisonResult.ranked_suppliers.map((s) => (
                          <th key={s.supplier_name} style={{ textAlign: "center" }}>
                            {s.supplier_name}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><strong>Rang final</strong></td>
                        {comparisonResult.ranked_suppliers.map((s) => (
                          <td key={s.supplier_name} style={{ textAlign: "center", fontWeight: "bold" }}>
                            #{s.rank}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td><strong>Score de risque (0=vertueux, 100=critique)</strong></td>
                        {comparisonResult.ranked_suppliers.map((s) => (
                          <td key={s.supplier_name} style={{ textAlign: "center" }}>
                            <span className={`risk-badge risk-${s.risk_score >= 70 ? "high" : s.risk_score >= 35 ? "med" : "low"}`}>
                              {s.risk_score}/100
                            </span>
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td><strong>Statut de conformité réglementaire</strong></td>
                        {comparisonResult.ranked_suppliers.map((s) => (
                          <td key={s.supplier_name} style={{ textAlign: "center" }}>
                            {s.overall_compliance.replaceAll("_", " ")}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td><strong>Infractions juridiques (AGEC / Conso)</strong></td>
                        {comparisonResult.ranked_suppliers.map((s) => (
                          <td key={s.supplier_name} style={{ textAlign: "center", color: s.violations_count > 0 ? "var(--red)" : "var(--green)" }}>
                            {s.violations_count} constat(s)
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td><strong>Preuve ACV ISO 14044 déclarée</strong></td>
                        {comparisonResult.ranked_suppliers.map((s) => (
                          <td key={s.supplier_name} style={{ textAlign: "center" }}>
                            {s.has_lca_declared ? "✓ Oui" : "— Non"}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td><strong>Écolabel officiel vérifié</strong></td>
                        {comparisonResult.ranked_suppliers.map((s) => (
                          <td key={s.supplier_name} style={{ textAlign: "center" }}>
                            {s.has_ecolabel_declared ? "✓ Oui (Safe Harbor)" : "— Non"}
                          </td>
                        ))}
                      </tr>
                      <tr>
                        <td><strong>Plafond d'amende encouru</strong></td>
                        {comparisonResult.ranked_suppliers.map((s) => (
                          <td key={s.supplier_name} style={{ textAlign: "center" }}>
                            {formatEur(s.max_known_fine_eur)}
                          </td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* VUE 2 : REGISTRE D'HISTORIQUE DES AUDITS */}
      {activeTab === "history" && (
        <div className="surface-card history-panel" style={{ marginTop: "16px" }}>
          <div className="history-header flex items-center justify-between flex-wrap gap-3">
            <div>
              <div className="section-eyebrow">REGISTRE IMMUABLE SHA-256</div>
              <h2 className="card-title">Historique des Audits & Piste Probatoire</h2>
              <p className="card-description">
                Tous les audits réalisés sont enregistrés et scellés par chaînage cryptographique.
              </p>
            </div>
            <div className="history-controls flex items-center gap-2 flex-wrap">
              <input
                type="text"
                placeholder="Rechercher fournisseur, SKU, audit..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") void loadHistory(); }}
                className="history-search-input"
              />
              <select
                value={complianceFilter}
                onChange={(e) => setComplianceFilter(e.target.value)}
                className="history-filter-select"
              >
                <option value="">Tous les statuts</option>
                <option value="COMPLIANT">Conforme</option>
                <option value="NON_COMPLIANT">Non conforme</option>
                <option value="REVIEW_REQUIRED">Revue requise</option>
                <option value="CONDITIONAL_REJECT">Rejet conditionnel</option>
              </select>
              <button type="button" className="button button-secondary" onClick={() => void loadHistory()}>
                Filtrer
              </button>
            </div>
          </div>

          {selectedAuditIds.length > 0 && (
            <div className="selected-audits-bar flex items-center justify-between" style={{ marginTop: "14px", padding: "10px 14px", background: "#f1f8ed", borderRadius: "8px", border: "1px solid #cce5c2" }}>
              <span>
                <strong>{selectedAuditIds.length}</strong> audit(s) sélectionné(s) pour comparaison.
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="button button-primary"
                  style={{ fontSize: "11px", padding: "6px 14px" }}
                  onClick={compareSelectedFromHistory}
                >
                  ⚖ Comparer ces fournisseurs
                </button>
                <button
                  type="button"
                  className="button button-quiet"
                  style={{ fontSize: "11px" }}
                  onClick={() => setSelectedAuditIds([])}
                >
                  Désélectionner
                </button>
              </div>
            </div>
          )}

          {historyError && <div className="benchmark-alert alert-error">{historyError}</div>}

          {isLoadingHistory ? (
            <div style={{ textAlign: "center", padding: "40px" }}>Chargement du registre...</div>
          ) : historyItems.length === 0 ? (
            <div style={{ textAlign: "center", padding: "40px", color: "var(--muted)" }}>
              Aucun audit archivé ne correspond aux critères. Lancez un premier audit pour initialiser le registre.
            </div>
          ) : (
            <div className="history-table-wrap" style={{ overflowX: "auto", marginTop: "16px" }}>
              <table className="history-table w-full">
                <thead>
                  <tr>
                    <th style={{ width: "36px" }}></th>
                    <th>Date d'évaluation</th>
                    <th>Fournisseur / SKU</th>
                    <th>Conformité</th>
                    <th>Risque</th>
                    <th>Infractions</th>
                    <th>Plafond amende</th>
                    <th>Empreinte SHA-256</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {historyItems.map((item) => {
                    const badge = COMPLIANCE_BADGES[item.overall_compliance] || {
                      label: item.overall_compliance,
                      color: "badge-gray",
                    };
                    const isSelected = selectedAuditIds.includes(item.audit_id);
                    return (
                      <tr key={item.audit_id} className={isSelected ? "row-selected" : ""}>
                        <td>
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleSelectAudit(item.audit_id)}
                            aria-label={`Sélectionner audit ${item.audit_id}`}
                          />
                        </td>
                        <td>
                          <div className="cell-date">
                            {new Date(item.created_at_utc).toLocaleString("fr-FR", {
                              day: "2-digit",
                              month: "2-digit",
                              year: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </div>
                          <small className="cell-audit-id">{item.audit_id.slice(0, 8)}</small>
                        </td>
                        <td>
                          <strong>{item.supplier_name || "Non spécifié"}</strong>
                          <div className="cell-sku">{item.product_identifier || "SKU par défaut"}</div>
                        </td>
                        <td>
                          <span className={`status-pill ${badge.color}`}>
                            {badge.label}
                          </span>
                        </td>
                        <td>
                          <span className={`score-badge ${item.risk_score >= 70 ? "badge-red" : item.risk_score >= 40 ? "badge-amber" : "badge-green"}`}>
                            {item.risk_score}/100
                          </span>
                        </td>
                        <td>
                          {item.violations_count > 0 ? (
                            <span style={{ color: "var(--red)", fontWeight: "bold" }}>
                              {item.violations_count} infraction(s)
                            </span>
                          ) : (
                            <span style={{ color: "var(--green)" }}>0</span>
                          )}
                        </td>
                        <td>{formatEur(item.max_fixed_fine_eur)}</td>
                        <td>
                          <code className="hash-code" title={item.record_hash}>
                            {item.record_hash.slice(0, 10)}…
                          </code>
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <div className="flex gap-1 justify-end">
                            <button
                              type="button"
                              className="button button-secondary"
                              style={{ padding: "4px 8px", fontSize: "10px" }}
                              onClick={() => handleLoadAudit(item.audit_id)}
                              disabled={loadingAuditId === item.audit_id}
                            >
                              {loadingAuditId === item.audit_id ? "…" : "Ouvrir"}
                            </button>
                            <button
                              type="button"
                              className="button button-quiet"
                              style={{ padding: "4px 8px", fontSize: "10px" }}
                              onClick={() => handleDownloadArchivedPdf(item.audit_id)}
                              title="Télécharger l'attestation PDF"
                            >
                              PDF
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
