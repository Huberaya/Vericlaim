"use client";

import { useEffect, useState } from "react";
import { getPreAuditReport } from "@/lib/api";
import type { PreAuditReportResponse } from "@/lib/types";

interface PreAuditReportModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function PreAuditReportModal({ isOpen, onClose }: PreAuditReportModalProps) {
  const [report, setReport] = useState<PreAuditReportResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    async function loadReport() {
      setIsLoading(true);
      setError(null);
      try {
        const data = await getPreAuditReport();
        setReport(data);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Erreur lors de la génération du rapport");
      } finally {
        setIsLoading(false);
      }
    }
    loadReport();
  }, [isOpen]);

  function handleDownloadJson() {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${report.report_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handlePrint() {
    window.print();
  }

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-5xl rounded-xl bg-white shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 flex flex-col max-h-[92vh]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded border border-indigo-200 dark:bg-indigo-950 dark:border-indigo-900 dark:text-indigo-400">
                {report?.report_id || "GÉNÉRATION DU RAPPORT..."}
              </span>
              <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                CONFIDENTIAL · PRÉ-AUDIT INTERNE
              </span>
            </div>
            <h2 className="text-lg font-bold text-slate-900 dark:text-white mt-1">
              Rapport Synthétique de Pré-Audit de Conformité Environnementale
            </h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6 text-xs">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
              {error}
            </div>
          )}

          {isLoading && (
            <div className="py-12 text-center text-slate-400">
              Génération du rapport et calcul des signatures cryptographiques...
            </div>
          )}

          {report && (
            <>
              {/* Mandatory Legal Disclaimer Banner */}
              <div className="rounded-xl border border-amber-300 bg-amber-50/90 p-4 dark:border-amber-800 dark:bg-amber-950/40 text-amber-950 dark:text-amber-200">
                <div className="flex items-start gap-2.5">
                  <span className="text-lg">⚖️</span>
                  <div>
                    <strong className="font-bold text-xs uppercase tracking-wider">
                      Avertissement Légal &amp; Statut du Document :
                    </strong>
                    <p className="mt-1 text-[11px] leading-relaxed">
                      {report.legal_disclaimer}
                    </p>
                  </div>
                </div>
              </div>

              {/* Dossier Meta & Signatures */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 bg-slate-50 p-4 rounded-xl border border-slate-200 dark:bg-slate-800/40 dark:border-slate-800">
                <div>
                  <span className="text-[10px] font-semibold text-slate-500 uppercase">Organisation</span>
                  <div className="font-bold text-slate-900 dark:text-white text-sm">{report.organization_name}</div>
                </div>
                <div>
                  <span className="text-[10px] font-semibold text-slate-500 uppercase">Date d&apos;évaluation</span>
                  <div className="font-bold text-slate-900 dark:text-white text-sm">
                    {new Date(report.as_of_date).toLocaleDateString("fr-FR")}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] font-semibold text-slate-500 uppercase">Version Rule Book</span>
                  <div className="font-mono font-bold text-slate-900 dark:text-white text-xs truncate">
                    {report.rulebook_version.slice(0, 18)}…
                  </div>
                </div>
                <div>
                  <span className="text-[10px] font-semibold text-slate-500 uppercase">Signature d&apos;Audit</span>
                  <div className="font-mono text-[10px] text-emerald-600 dark:text-emerald-400 truncate">
                    {report.audit_trail_signature.slice(0, 20)}…
                  </div>
                </div>
              </div>

              {/* KPI Summary Grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="rounded-lg border border-slate-200 bg-white p-3.5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                  <span className="text-[10px] font-semibold uppercase text-slate-500">Taux de Conformité Validé</span>
                  <div className="text-2xl font-bold text-indigo-600 dark:text-indigo-400 mt-1">
                    {report.summary_kpis.global_compliance_rate_percent}%
                  </div>
                </div>
                <div className="rounded-lg border border-emerald-200 bg-emerald-50/50 p-3.5 dark:border-emerald-900/40 dark:bg-emerald-950/20">
                  <span className="text-[10px] font-semibold uppercase text-emerald-700 dark:text-emerald-400">Allégations Validées</span>
                  <div className="text-2xl font-bold text-emerald-700 dark:text-emerald-400 mt-1">
                    {report.summary_kpis.claims_validated} / {report.summary_kpis.total_claims_detected}
                  </div>
                </div>
                <div className="rounded-lg border border-rose-200 bg-rose-50/50 p-3.5 dark:border-rose-900/40 dark:bg-rose-950/20">
                  <span className="text-[10px] font-semibold uppercase text-rose-700 dark:text-rose-400">Allégations Contestées</span>
                  <div className="text-2xl font-bold text-rose-700 dark:text-rose-400 mt-1">
                    {report.summary_kpis.claims_contested}
                  </div>
                </div>
                <div className="rounded-lg border border-amber-200 bg-amber-50/50 p-3.5 dark:border-amber-900/40 dark:bg-amber-950/20">
                  <span className="text-[10px] font-semibold uppercase text-amber-700 dark:text-amber-400">Preuves Manquantes</span>
                  <div className="text-2xl font-bold text-amber-700 dark:text-amber-400 mt-1">
                    {report.summary_kpis.claims_missing_evidence}
                  </div>
                </div>
              </div>

              {/* Remediation Summary */}
              <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-4 dark:border-indigo-900/50 dark:bg-indigo-950/20 space-y-2">
                <h3 className="font-bold text-indigo-950 dark:text-indigo-200 uppercase text-xs tracking-wider flex items-center gap-2">
                  <span>💡</span> Plan d&apos;Action &amp; Remédiations Prioritaires
                </h3>
                <ul className="list-disc list-inside text-xs text-slate-800 dark:text-slate-200 space-y-1">
                  {report.remediation_summary.map((rec, i) => (
                    <li key={i}>{rec}</li>
                  ))}
                </ul>
              </div>

              {/* Detailed Findings Table */}
              <div className="space-y-2">
                <h3 className="font-bold text-slate-800 dark:text-slate-200 uppercase text-xs tracking-wider">
                  Détail des Constats &amp; Évaluations Allégations ({report.findings.length})
                </h3>
                <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
                  <table className="w-full text-left text-xs">
                    <thead className="border-b border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
                      <tr>
                        <th className="py-3 px-3 font-semibold">Allégation analysée</th>
                        <th className="py-3 px-3 font-semibold">Base légale &amp; Sévérité</th>
                        <th className="py-3 px-3 font-semibold">Preuve &amp; Arbitrage</th>
                        <th className="py-3 px-3 font-semibold">Conseil de remédiation</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                      {report.findings.map((finding, idx) => (
                        <tr key={idx} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/40">
                          <td className="py-3 px-3 max-w-xs font-semibold text-slate-900 dark:text-white">
                            &ldquo;{finding.claim_text}&rdquo;
                            <span className="block font-normal text-[10px] text-slate-400 uppercase mt-0.5">
                              {finding.category}
                            </span>
                          </td>
                          <td className="py-3 px-3 whitespace-nowrap">
                            <span
                              className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                finding.severity === "CRITICAL"
                                  ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                                  : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                              }`}
                            >
                              {finding.severity}
                            </span>
                            <div className="text-[10px] text-slate-500 mt-1">{finding.legal_basis}</div>
                          </td>
                          <td className="py-3 px-3 whitespace-nowrap">
                            <div className="text-[11px] font-medium text-slate-800 dark:text-slate-200">
                              Couverture : <span className="font-bold">{finding.coverage_status}</span>
                            </div>
                            <div className="text-[10px] text-slate-500 mt-0.5">
                              Arbitrage : <span className="capitalize font-semibold">{finding.validation_decision}</span>
                            </div>
                          </td>
                          <td className="py-3 px-3 text-slate-700 dark:text-slate-300 text-[11px]">
                            {finding.remediation_advice}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-slate-200 bg-slate-50 px-6 py-4 dark:border-slate-800 dark:bg-slate-900/50 flex items-center justify-between">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleDownloadJson}
              disabled={!report}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            >
              📥 Exporter JSON
            </button>
            <button
              type="button"
              onClick={handlePrint}
              disabled={!report}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            >
              🖨️ Imprimer / PDF
            </button>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700"
          >
            Fermer le rapport
          </button>
        </div>
      </div>
    </div>
  );
}
