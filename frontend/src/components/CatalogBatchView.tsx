"use client";

import { useState } from "react";
import { auditCatalogBatch, auditCatalogCsv, exportCatalogBatchCsv, downloadCatalogBatchExcel } from "@/lib/api";
import type { CatalogBatchResponse, CatalogItemInput, OverallCompliance, RegulatoryAuditResponse } from "@/lib/types";

type Props = {
  onLoadAuditText?: (text: string) => void;
};

const COMPLIANCE_BADGES: Record<OverallCompliance, { label: string; color: string }> = {
  COMPLIANT: { label: "CONFORME", color: "badge-green" },
  REVIEW_REQUIRED: { label: "PREUVES REQUISES", color: "badge-amber" },
  CONDITIONAL_REJECT: { label: "SUSPENDU", color: "badge-amber" },
  UPCOMING_REQUIREMENTS: { label: "À PRÉPARER (UE)", color: "badge-amber" },
  NON_COMPLIANT: { label: "NON CONFORME", color: "badge-red" },
  NO_CLAIMS_DETECTED: { label: "SANS ALLÉGATION", color: "badge-gray" },
};

const DEMO_CATALOG: CatalogItemInput[] = [
  {
    sku: "SKU-PACK-001",
    title: "Gourde Nomade Écologique 750ml",
    text: "Bouteille 100% biodégradable et sans déchet pour la planète. Emballage en plastique recyclé.",
    surface: "packaging",
    supplier_name: "EcoPlast Global",
  },
  {
    sku: "SKU-COSM-002",
    title: "Shampoing Solide Bio & Éthique",
    text: "Formule 100% sans produits chimiques, zéro pollution pour l'océan, emballage compostable.",
    surface: "online_store",
    supplier_name: "NatureCare Lab",
  },
  {
    sku: "SKU-CLEAN-003",
    title: "Lessive Végétale Concentrée 1L",
    text: "Flacon en plastique oxo-dégradable respectueux de la nature, neutre en carbone par compensation.",
    surface: "packaging",
    supplier_name: "GreenClean France",
  },
  {
    sku: "SKU-FOOD-004",
    title: "Bocal en Verre Consigné 500g",
    text: "Emballage certifié Ecolabel Européen (licence FR/012/345). Réduction de 28% de CO2 (ACV ISO 14044 tiers).",
    surface: "packaging",
    supplier_name: "VerreDurable SAS",
    has_lca: true,
    ecolabel_license: "FR/012/345",
  },
  {
    sku: "SKU-TEXT-005",
    title: "T-Shirt Coton Bio Éco-responsable",
    text: "Tissu respectueux de l'environnement, 100% naturel, confection éthique sans impact carbone.",
    surface: "online_store",
    supplier_name: "TexBio Europe",
  },
  {
    sku: "SKU-BOX-006",
    title: "Carton d'Expédition Recyclé",
    text: "Boîte postale en carton recyclé, certifiée Blauer Engel DE-UZ-14b, recyclable dans le bac jaune.",
    surface: "packaging",
    supplier_name: "Cartonnerie Rhodanienne",
    ecolabel_license: "DE-UZ-14b",
  },
];

export default function CatalogBatchView({ onLoadAuditText }: Props) {
  const [report, setReport] = useState<CatalogBatchResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  async function runBatch(items: CatalogItemInput[]) {
    setIsLoading(true);
    setError(null);
    try {
      const res = await auditCatalogBatch({
        items,
        jurisdiction: "FR",
        consumer_facing: true,
      });
      setReport(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur lors de l'audit du catalogue");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleFileUpload(file: File) {
    setSelectedFile(file);
    setIsLoading(true);
    setError(null);
    try {
      const res = await auditCatalogCsv(file);
      setReport(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur de traitement du fichier CSV");
    } finally {
      setIsLoading(false);
    }
  }

  function downloadSampleCsv() {
    const csvContent =
      "sku;title;text;surface;supplier;has_lca;ecolabel\n" +
      'SKU-DEMO-01;Gourde Nomade;"Bouteille 100% biodégradable et sans déchet pour la planète.";packaging;EcoPlast;false;\n' +
      'SKU-DEMO-02;Shampoing Solide;"Formule garantie 100% sans produits chimiques, zéro pollution.";online_store;NatureCare;false;\n' +
      'SKU-DEMO-03;Bocal Consigné;"Certifié Ecolabel Européen (licence FR/012/345). Réduction 28% CO2.";packaging;VerreDurable;true;FR/012/345\n';

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "modele_catalogue_vericlaim.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  const filteredResults = report?.results.filter((item) => {
    const matchesSearch =
      searchTerm === "" ||
      item.sku.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (item.supplier_name && item.supplier_name.toLowerCase().includes(searchTerm.toLowerCase()));

    const matchesStatus =
      statusFilter === "ALL" ||
      item.overall_compliance === statusFilter;

    return matchesSearch && matchesStatus;
  }) ?? [];

  return (
    <div className="space-y-6">
      <div className="surface-card">
        <div className="card-heading">
          <div>
            <div className="section-eyebrow">
              <span className="step-chip">CATALOGUE</span> AUDIT EN MASSE (BATCH)
            </div>
            <h2 className="card-title">Audit Réglementaire de Catalogue Produit (CSV & JSON)</h2>
            <p className="card-description">
              Auditez simultanément des dizaines ou centaines de fiches produits pour identifier les allégations
              environnementales interdites (AGEC, Directive UE 2024/825) et évaluer le risque financier cumulé.
            </p>
          </div>
          <span className="engine-tag">
            <span className="pulse-dot" /> Moteur déterministe
          </span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem", marginTop: "1rem" }}>
          {/* Option A: Upload CSV */}
          <div style={{ border: "1px dashed var(--color-border, #cbd5e1)", borderRadius: "8px", padding: "1.5rem", textAlign: "center" }}>
            <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>📁</div>
            <h3 style={{ fontSize: "1rem", fontWeight: "600", marginBottom: "0.25rem" }}>Importer un fichier CSV</h3>
            <p style={{ fontSize: "0.8rem", color: "var(--color-text-muted, #64748b)", marginBottom: "1rem" }}>
              Colonnes : <code>sku, title, text, surface, supplier</code> (séparateur virgule ou point-virgule)
            </p>
            <input
              type="file"
              accept=".csv,text/csv"
              style={{ display: "none" }}
              id="catalog-csv-input"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFileUpload(f);
              }}
              disabled={isLoading}
            />
            <div style={{ display: "flex", gap: "0.5rem", justifyContent: "center" }}>
              <label
                htmlFor="catalog-csv-input"
                className="button button-primary"
                style={{ cursor: isLoading ? "not-allowed" : "pointer" }}
              >
                {isLoading ? "Traitement en cours…" : "Choisir un fichier CSV"}
              </label>
              <button
                type="button"
                className="button button-quiet"
                onClick={downloadSampleCsv}
                disabled={isLoading}
              >
                Télécharger le modèle CSV
              </button>
            </div>
            {selectedFile && (
              <p style={{ fontSize: "0.8rem", marginTop: "0.75rem", color: "var(--color-brand, #059669)" }}>
                Fichier sélectionné : {selectedFile.name} ({Math.round(selectedFile.size / 1024)} Ko)
              </p>
            )}
          </div>

          {/* Option B: Demo Catalog */}
          <div style={{ border: "1px solid var(--color-border, #e2e8f0)", borderRadius: "8px", padding: "1.5rem", background: "var(--color-surface-subtle, #f8fafc)" }}>
            <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>⚡</div>
            <h3 style={{ fontSize: "1rem", fontWeight: "600", marginBottom: "0.25rem" }}>Catalogue de Démonstration</h3>
            <p style={{ fontSize: "0.8rem", color: "var(--color-text-muted, #64748b)", marginBottom: "1rem" }}>
              Testez immédiatement le moteur sur un catalogue type de 6 produits (emballages, cosmétiques, détergents, textile).
            </p>
            <button
              type="button"
              className="button button-secondary"
              style={{ width: "100%" }}
              onClick={() => runBatch(DEMO_CATALOG)}
              disabled={isLoading}
            >
              {isLoading ? "Analyse du lot en cours…" : "Charger le catalogue démo (6 produits)"}
            </button>
          </div>
        </div>

        {error && (
          <p className="inline-error" style={{ marginTop: "1rem" }} role="alert">
            {error}
          </p>
        )}
      </div>

      {/* KPI Cards when report is ready */}
      {report && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "1rem" }}>
            <div className="surface-card" style={{ padding: "1rem" }}>
              <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)", textTransform: "uppercase" }}>
                Total Références
              </span>
              <div style={{ fontSize: "1.75rem", fontWeight: "700", marginTop: "0.25rem" }}>
                {report.total_items}
              </div>
              <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)" }}>
                audits déterministes exécutés
              </span>
            </div>

            <div className="surface-card" style={{ padding: "1rem" }}>
              <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)", textTransform: "uppercase" }}>
                Taux de Conformité
              </span>
              <div
                style={{
                  fontSize: "1.75rem",
                  fontWeight: "700",
                  marginTop: "0.25rem",
                  color: report.compliance_rate_pct >= 80 ? "#059669" : report.compliance_rate_pct >= 50 ? "#d97706" : "#dc2626",
                }}
              >
                {report.compliance_rate_pct}%
              </div>
              <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)" }}>
                {report.compliant_items} produit(s) conforme(s)
              </span>
            </div>

            <div className="surface-card" style={{ padding: "1rem" }}>
              <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)", textTransform: "uppercase" }}>
                Non-Conformités Critiques
              </span>
              <div style={{ fontSize: "1.75rem", fontWeight: "700", marginTop: "0.25rem", color: "#dc2626" }}>
                {report.non_compliant_items}
              </div>
              <span style={{ fontSize: "0.75rem", color: "#dc2626" }}>
                allégations interdites constatées
              </span>
            </div>

            <div className="surface-card" style={{ padding: "1rem" }}>
              <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)", textTransform: "uppercase" }}>
                Plafond d'Amendes Cumulé
              </span>
              <div style={{ fontSize: "1.75rem", fontWeight: "700", marginTop: "0.25rem", color: "#b91c1c" }}>
                {report.total_fines_ceiling_eur.toLocaleString("fr-FR")} €
              </div>
              <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)" }}>
                exposition administrative maximale
              </span>
            </div>

            <div className="surface-card" style={{ padding: "1rem" }}>
              <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)", textTransform: "uppercase" }}>
                Score de Risque Moyen
              </span>
              <div style={{ fontSize: "1.75rem", fontWeight: "700", marginTop: "0.25rem" }}>
                {report.average_risk_score} / 100
              </div>
              <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)" }}>
                pondération sévérité et infractions
              </span>
            </div>
          </div>

          {/* Table of Results */}
          <div className="surface-card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap", gap: "0.5rem" }}>
              <div>
                <h3 style={{ fontSize: "1.1rem", fontWeight: "600" }}>Résultats Détaillés par Référence Produit</h3>
                <p style={{ fontSize: "0.8rem", color: "var(--color-text-muted, #64748b)" }}>
                  {filteredResults.length} sur {report.total_items} référence(s) affichée(s)
                </p>
              </div>

              <div style={{ display: "flex", gap: "0.5rem" }}>
                <input
                  type="text"
                  placeholder="Filtrer par SKU, titre ou fournisseur…"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  style={{
                    padding: "0.35rem 0.75rem",
                    fontSize: "0.85rem",
                    border: "1px solid var(--color-border, #cbd5e1)",
                    borderRadius: "6px",
                    minWidth: "220px",
                  }}
                />
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  style={{
                    padding: "0.35rem 0.75rem",
                    fontSize: "0.85rem",
                    border: "1px solid var(--color-border, #cbd5e1)",
                    borderRadius: "6px",
                  }}
                >
                  <option value="ALL">Tous les statuts</option>
                  <option value="COMPLIANT">Conforme</option>
                  <option value="NON_COMPLIANT">Non Conforme</option>
                  <option value="REVIEW_REQUIRED">Preuves Requises</option>
                </select>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => exportCatalogBatchCsv(report)}
                >
                  📥 CSV
                </button>
                <button
                  type="button"
                  className="button"
                  style={{
                    padding: "6px 12px",
                    fontSize: "0.82rem",
                    background: "#047857",
                    color: "#fff",
                    border: 0,
                    borderRadius: "6px",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                  onClick={async () => {
                    try {
                      await downloadCatalogBatchExcel(report);
                    } catch (err) {
                      alert(err instanceof Error ? err.message : "Erreur téléchargement Excel");
                    }
                  }}
                >
                  📊 Excel (.xlsx)
                </button>
              </div>
            </div>

            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
                <thead>
                  <tr style={{ borderBottom: "2px solid var(--color-border, #e2e8f0)", textAlign: "left" }}>
                    <th style={{ padding: "0.6rem 0.5rem" }}>SKU</th>
                    <th style={{ padding: "0.6rem 0.5rem" }}>Produit</th>
                    <th style={{ padding: "0.6rem 0.5rem" }}>Fournisseur</th>
                    <th style={{ padding: "0.6rem 0.5rem" }}>Statut</th>
                    <th style={{ padding: "0.6rem 0.5rem", textAlign: "center" }}>Score Risque</th>
                    <th style={{ padding: "0.6rem 0.5rem", textAlign: "center" }}>Infractions</th>
                    <th style={{ padding: "0.6rem 0.5rem", textAlign: "right" }}>Plafond (€)</th>
                    <th style={{ padding: "0.6rem 0.5rem" }}>Allégations Relevées</th>
                    <th style={{ padding: "0.6rem 0.5rem", textAlign: "center" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredResults.map((item) => {
                    const badge = COMPLIANCE_BADGES[item.overall_compliance] || {
                      label: item.overall_compliance,
                      color: "badge-amber",
                    };
                    return (
                      <tr
                        key={item.sku}
                        style={{
                          borderBottom: "1px solid var(--color-border, #f1f5f9)",
                          backgroundColor:
                            item.overall_compliance === "NON_COMPLIANT"
                              ? "rgba(239, 68, 68, 0.03)"
                              : item.overall_compliance === "COMPLIANT"
                              ? "rgba(16, 185, 129, 0.03)"
                              : "transparent",
                        }}
                      >
                        <td style={{ padding: "0.6rem 0.5rem", fontWeight: "600", fontFamily: "monospace" }}>
                          {item.sku}
                        </td>
                        <td style={{ padding: "0.6rem 0.5rem", fontWeight: "500" }}>{item.title}</td>
                        <td style={{ padding: "0.6rem 0.5rem", color: "var(--color-text-muted, #64748b)" }}>
                          {item.supplier_name || "—"}
                        </td>
                        <td style={{ padding: "0.6rem 0.5rem" }}>
                          <span className={`compliance-badge ${badge.color}`}>{badge.label}</span>
                        </td>
                        <td
                          style={{
                            padding: "0.6rem 0.5rem",
                            textAlign: "center",
                            fontWeight: "700",
                            color:
                              item.risk_score >= 70
                                ? "#dc2626"
                                : item.risk_score >= 40
                                ? "#d97706"
                                : "#059669",
                          }}
                        >
                          {item.risk_score}
                        </td>
                        <td style={{ padding: "0.6rem 0.5rem", textAlign: "center" }}>
                          {item.violations_count > 0 ? (
                            <span style={{ color: "#dc2626", fontWeight: "600" }}>
                              {item.violations_count}
                            </span>
                          ) : (
                            <span style={{ color: "#059669" }}>0</span>
                          )}
                        </td>
                        <td style={{ padding: "0.6rem 0.5rem", textAlign: "right", fontWeight: "600" }}>
                          {item.fines_ceiling_eur > 0
                            ? `${item.fines_ceiling_eur.toLocaleString("fr-FR")} €`
                            : "0 €"}
                        </td>
                        <td style={{ padding: "0.6rem 0.5rem", maxWidth: "250px" }}>
                          {item.claims.length > 0 ? (
                            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.2rem" }}>
                              {item.claims.slice(0, 2).map((c, idx) => (
                                <span
                                  key={idx}
                                  style={{
                                    fontSize: "0.7rem",
                                    padding: "0.15rem 0.35rem",
                                    background: "rgba(0,0,0,0.05)",
                                    borderRadius: "4px",
                                  }}
                                  title={c}
                                >
                                  {c.length > 28 ? `${c.slice(0, 28)}…` : c}
                                </span>
                              ))}
                              {item.claims.length > 2 && (
                                <span style={{ fontSize: "0.7rem", color: "var(--color-text-muted, #64748b)" }}>
                                  +{item.claims.length - 2}
                                </span>
                              )}
                            </div>
                          ) : (
                            <span style={{ color: "var(--color-text-muted, #64748b)", fontSize: "0.75rem" }}>
                              Aucune allégation
                            </span>
                          )}
                        </td>
                        <td style={{ padding: "0.6rem 0.5rem", textAlign: "center" }}>
                          {onLoadAuditText && (
                            <button
                              type="button"
                              className="button button-quiet"
                              style={{ fontSize: "0.75rem", padding: "0.25rem 0.4rem" }}
                              onClick={() => {
                                const matched = DEMO_CATALOG.find((d) => d.sku === item.sku);
                                if (matched) onLoadAuditText(matched.text);
                              }}
                            >
                              Inspecter ↗
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
