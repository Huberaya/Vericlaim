"use client";

import { useEffect, useState } from "react";
import { exportPilotDossier, getPilotOverview, getRetentionPolicy } from "@/lib/api";
import type { PilotOverviewResponse, RetentionPolicyResponse } from "@/lib/types";
import { CatalogBatchImportModal } from "./CatalogBatchImportModal";
import { PreAuditReportModal } from "./PreAuditReportModal";

interface PilotExecutiveSummaryPanelProps {
  canManageCatalog?: boolean;
}

export function PilotExecutiveSummaryPanel({ canManageCatalog = false }: PilotExecutiveSummaryPanelProps) {
  const [overview, setOverview] = useState<PilotOverviewResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modals
  const [isReportOpen, setIsReportOpen] = useState(false);
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [retentionPolicy, setRetentionPolicy] = useState<RetentionPolicyResponse | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  async function loadData() {
    setIsLoading(true);
    setError(null);
    try {
      const data = await getPilotOverview();
      setOverview(data);
    } catch (err: unknown) {
      console.error("Failed to load pilot overview:", err);
      setError(err instanceof Error ? err.message : "Erreur lors du chargement du tableau de bord pilote");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  async function handleExportDossier() {
    setIsExporting(true);
    try {
      const dossier = await exportPilotDossier();
      const blob = new Blob([JSON.stringify(dossier, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `vericlaim-dossier-export-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur lors de l'export du dossier");
    } finally {
      setIsExporting(false);
    }
  }

  async function handleShowRetention() {
    try {
      const policy = await getRetentionPolicy();
      setRetentionPolicy(policy);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur de chargement de la politique");
    }
  }

  const kpis = overview?.kpis;

  return (
    <div className="space-y-6">
      {/* Executive Header & Quick Actions */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded bg-indigo-100 px-2.5 py-0.5 text-xs font-bold text-indigo-800 dark:bg-indigo-950/80 dark:text-indigo-300">
              PACK PILOTE B2B
            </span>
            <span className="text-xs text-slate-500 font-medium">
              {overview?.organization_name}
            </span>
          </div>
          <h2 className="text-xl font-bold text-slate-900 dark:text-white mt-1 flex items-center gap-2">
            Tableau de Bord 360° du Risque de Conformité
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Suivi consolidé des allégations, couverture probatoire, fournisseurs et remédiations (Chantier 8).
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canManageCatalog && (
            <button
              onClick={() => setIsImportOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700 transition"
            >
              <span>📥</span> Import Catalogue (Batch)
            </button>
          )}

          <button
            onClick={handleExportDossier}
            disabled={isExporting}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700 transition"
          >
            <span>💾</span> {isExporting ? "Export..." : "Export Dossier"}
          </button>

          <button
            onClick={() => setIsReportOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 transition"
          >
            <span>📄</span> Rapport Pré-Audit Exécutif
          </button>
        </div>
      </div>

      {/* Main KPI Gauges & Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* Compliance Rate Gauge Card */}
        <div className="rounded-xl border border-indigo-200 bg-gradient-to-br from-indigo-50/50 to-white p-5 shadow-sm dark:border-indigo-900/40 dark:from-indigo-950/20 dark:to-slate-900 flex flex-col justify-between">
          <div>
            <span className="text-xs font-semibold uppercase tracking-wider text-indigo-700 dark:text-indigo-400">
              Score de Conformité Global
            </span>
            <div className="mt-2 text-3xl font-extrabold text-indigo-950 dark:text-indigo-200">
              {kpis?.global_compliance_rate_percent ?? 0}%
            </div>
          </div>
          <div className="mt-4">
            <div className="h-2 w-full rounded-full bg-slate-200 dark:bg-slate-700 overflow-hidden">
              <div
                className="h-full rounded-full bg-indigo-600 transition-all duration-500"
                style={{ width: `${kpis?.global_compliance_rate_percent ?? 0}%` }}
              />
            </div>
            <p className="mt-1.5 text-[11px] text-slate-500">
              {kpis?.claims_validated ?? 0} validée(s) sur {kpis?.total_claims_detected ?? 0} allégation(s)
            </p>
          </div>
        </div>

        {/* Human Arbitrage Breakdown */}
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 flex flex-col justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Arbitrage &amp; Décisions
          </span>
          <div className="space-y-1.5 my-2 text-xs">
            <div className="flex justify-between items-center">
              <span className="text-emerald-700 dark:text-emerald-400 font-medium">✓ Validées</span>
              <strong className="font-bold">{kpis?.claims_validated ?? 0}</strong>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-rose-700 dark:text-rose-400 font-medium">✕ Contestées</span>
              <strong className="font-bold">{kpis?.claims_contested ?? 0}</strong>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-amber-700 dark:text-amber-400 font-medium">⏳ En attente</span>
              <strong className="font-bold">{kpis?.claims_pending_review ?? 0}</strong>
            </div>
          </div>
          <p className="text-[10px] text-slate-400 italic">Gouvernance Human-in-the-loop</p>
        </div>

        {/* Evidence Registry Coverage */}
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 flex flex-col justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Couverture Probatoire
          </span>
          <div className="space-y-1.5 my-2 text-xs">
            <div className="flex justify-between items-center">
              <span className="text-emerald-700 dark:text-emerald-400 font-medium">● Pièces rattachées</span>
              <strong className="font-bold">{kpis?.claims_with_sufficient_evidence ?? 0}</strong>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-rose-700 dark:text-rose-400 font-medium">✕ Sans justificatif</span>
              <strong className="font-bold">{kpis?.claims_missing_evidence ?? 0}</strong>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-amber-700 dark:text-amber-400 font-medium">⚠ Preuves expirées</span>
              <strong className="font-bold">{kpis?.claims_expired_evidence ?? 0}</strong>
            </div>
          </div>
          <p className="text-[10px] text-slate-400 italic">Matrice allégation ↔ preuve</p>
        </div>

        {/* Actionable Requests & Critical Alerts */}
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 flex flex-col justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Demandes Fournisseurs &amp; Alertes
          </span>
          <div className="space-y-1.5 my-2 text-xs">
            <div className="flex justify-between items-center">
              <span className="text-blue-700 dark:text-blue-400 font-medium">✉️ Demandes en cours</span>
              <strong className="font-bold">{kpis?.pending_evidence_requests ?? 0}</strong>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-rose-700 dark:text-rose-400 font-medium">⚠️ Demandes en retard</span>
              <strong className="font-bold">{kpis?.overdue_evidence_requests ?? 0}</strong>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-red-700 dark:text-red-400 font-medium">🚨 Allégations critiques</span>
              <strong className="font-bold">{kpis?.critical_risk_claims_count ?? 0}</strong>
            </div>
          </div>
          <p className="text-[10px] text-slate-400 italic">Suivi contradictoire</p>
        </div>
      </div>

      {/* Top Risk Suppliers Section */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-bold text-slate-900 dark:text-white text-sm flex items-center gap-2">
              <span>🏢</span> Portefeuille Fournisseurs &amp; Exposition au Risque
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Fournisseurs nécessitant des compléments probatoires (bilans ACV, attestations, écolabels).
            </p>
          </div>
          <button
            type="button"
            onClick={handleShowRetention}
            className="text-xs font-semibold text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200 underline"
          >
            🔒 Rétention &amp; RGPD
          </button>
        </div>

        <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
              <tr>
                <th className="py-2.5 px-3 font-semibold">Raison Sociale Fournisseur</th>
                <th className="py-2.5 px-3 font-semibold">Pays</th>
                <th className="py-2.5 px-3 font-semibold">Références Produits</th>
                <th className="py-2.5 px-3 font-semibold">Allégations associées</th>
                <th className="py-2.5 px-3 font-semibold">Preuves Manquantes</th>
                <th className="py-2.5 px-3 font-semibold text-right">Exposition</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {overview?.top_risk_suppliers.map((s) => (
                <tr key={s.supplier_id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/40">
                  <td className="py-2.5 px-3 font-semibold text-slate-900 dark:text-white">
                    {s.supplier_name}
                  </td>
                  <td className="py-2.5 px-3 uppercase text-slate-500">
                    {s.country_code || "FR"}
                  </td>
                  <td className="py-2.5 px-3 text-slate-700 dark:text-slate-300 font-medium">
                    {s.products_count} produit(s)
                  </td>
                  <td className="py-2.5 px-3 text-slate-700 dark:text-slate-300 font-medium">
                    {s.claims_count} allégation(s)
                  </td>
                  <td className="py-2.5 px-3">
                    {s.missing_evidence_count > 0 ? (
                      <span className="font-semibold text-rose-600 dark:text-rose-400">
                        {s.missing_evidence_count} manquante(s)
                      </span>
                    ) : (
                      <span className="text-emerald-600 dark:text-emerald-400">✓ Dossier complet</span>
                    )}
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    <span
                      className={`rounded px-2 py-0.5 text-[10px] font-bold uppercase ${
                        s.risk_level === "high"
                          ? "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300"
                          : s.risk_level === "medium"
                          ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                          : "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                      }`}
                    >
                      {s.risk_level === "high" ? "Risque Élevé" : s.risk_level === "medium" ? "Risque Moyen" : "Conforme"}
                    </span>
                  </td>
                </tr>
              ))}
              {overview?.top_risk_suppliers.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-4 text-center text-slate-400">
                    Aucun fournisseur enregistré pour le moment.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Retention Policy Modal Dialog */}
      {retentionPolicy && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4">
          <div className="relative w-full max-w-lg rounded-xl bg-white p-6 shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 space-y-4 text-xs">
            <h3 className="text-base font-bold text-slate-900 dark:text-white flex items-center gap-2">
              <span>🔒</span> Politique de Conservation des Données &amp; RGPD
            </h3>
            <div className="space-y-2 text-slate-700 dark:text-slate-300">
              <p><strong>Région d&apos;hébergement :</strong> {retentionPolicy.storage_region}</p>
              <p><strong>Standard de chiffrement :</strong> {retentionPolicy.encryption_standard}</p>
              <p><strong>Rétention des documents &amp; preuves :</strong> {retentionPolicy.documents_retention_years} ans</p>
              <p><strong>Rétention de la piste d&apos;audit (SHA-256) :</strong> {retentionPolicy.audit_trail_retention_years} ans</p>
              <p><strong>Contact DPO / Droits d&apos;accès :</strong> {retentionPolicy.gdpr_contact_email}</p>
              <p><strong>Formats d&apos;export supportés :</strong> {retentionPolicy.export_formats_supported.join(", ")}</p>
            </div>
            <div className="flex justify-end pt-2 border-t border-slate-200 dark:border-slate-800">
              <button
                type="button"
                onClick={() => setRetentionPolicy(null)}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700"
              >
                Fermer
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Pre-Audit Report Modal */}
      {isReportOpen && (
        <PreAuditReportModal
          isOpen={true}
          onClose={() => setIsReportOpen(false)}
        />
      )}

      {/* Catalog Batch Import Modal */}
      {isImportOpen && (
        <CatalogBatchImportModal
          isOpen={true}
          onClose={() => setIsImportOpen(false)}
          onImported={() => loadData()}
        />
      )}
    </div>
  );
}
