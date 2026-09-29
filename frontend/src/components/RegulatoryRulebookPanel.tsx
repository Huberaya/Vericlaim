"use client";

import { useEffect, useState } from "react";
import { getRuleBookOverview, listRegulatoryRules } from "@/lib/api";
import type {
  Jurisdiction,
  LegalStatus,
  RegulatoryRuleSummary,
  RuleBookSummaryResponse,
} from "@/lib/types";
import { RuleDetailModal } from "./RuleDetailModal";
import { RuleDiffModal } from "./RuleDiffModal";

interface RegulatoryRulebookPanelProps {
  canManage?: boolean;
}

export function RegulatoryRulebookPanel({ canManage = false }: RegulatoryRulebookPanelProps) {
  const [summary, setSummary] = useState<RuleBookSummaryResponse | null>(null);
  const [rules, setRules] = useState<RegulatoryRuleSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [jurisdictionFilter, setJurisdictionFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [searchTerm, setSearchTerm] = useState("");

  // Modals
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null);
  const [isChangelogOpen, setIsChangelogOpen] = useState(false);

  async function loadData() {
    setIsLoading(true);
    setError(null);
    try {
      const [sumRes, rulesRes] = await Promise.all([
        getRuleBookOverview(),
        listRegulatoryRules({
          jurisdiction: jurisdictionFilter !== "all" ? (jurisdictionFilter as Jurisdiction) : undefined,
          legal_status: statusFilter !== "all" ? (statusFilter as LegalStatus) : undefined,
          severity: severityFilter !== "all" ? severityFilter : undefined,
          search: searchTerm || undefined,
        }),
      ]);
      setSummary(sumRes);
      setRules(rulesRes);
    } catch (err: unknown) {
      console.error("Failed to load regulatory rulebook:", err);
      setError(err instanceof Error ? err.message : "Erreur de chargement du référentiel.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, [jurisdictionFilter, statusFilter, severityFilter, searchTerm]);

  return (
    <div className="space-y-6">
      {/* Overview & Governance Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
            <span className="text-indigo-600 dark:text-indigo-400">⚖️</span>
            Référentiel Réglementaire Versionné (Rule Book)
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Corpus de règles déterministes, citations exactes Légifrance / EUR-Lex et gouvernance de conformité (Chantier 7).
          </p>
        </div>

        <button
          onClick={() => setIsChangelogOpen(true)}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700 transition"
        >
          <span>📜</span> Journal des versions (Changelog &amp; Diff)
        </button>
      </div>

      {/* KPI Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Total Règles Actives
          </span>
          <div className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">
            {summary?.total_rules ?? rules.length}
          </div>
          <p className="mt-0.5 text-[10px] font-mono text-slate-400 truncate">
            {summary?.rulebook_version.slice(0, 16)}…
          </p>
        </div>

        <div className="rounded-xl border border-blue-200 bg-blue-50/50 p-4 shadow-sm dark:border-blue-900/50 dark:bg-blue-950/20">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-blue-700 dark:text-blue-400">
            🇫🇷 Droit Français (AGEC)
          </span>
          <div className="mt-1 text-2xl font-bold text-blue-700 dark:text-blue-400">
            {summary?.jurisdiction_breakdown["FR"] ?? 3}
          </div>
          <p className="mt-0.5 text-[10px] text-blue-600 dark:text-blue-500">Loi AGEC &amp; Climat</p>
        </div>

        <div className="rounded-xl border border-indigo-200 bg-indigo-50/50 p-4 shadow-sm dark:border-indigo-900/50 dark:bg-indigo-950/20">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-indigo-700 dark:text-indigo-400">
            🇪🇺 Droit Européen (EmpCo)
          </span>
          <div className="mt-1 text-2xl font-bold text-indigo-700 dark:text-indigo-400">
            {summary?.jurisdiction_breakdown["EU"] ?? 3}
          </div>
          <p className="mt-0.5 text-[10px] text-indigo-600 dark:text-indigo-500">Directive 2024/825</p>
        </div>

        <div className="rounded-xl border border-amber-200 bg-amber-50/50 p-4 shadow-sm dark:border-amber-900/50 dark:bg-amber-950/20">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
            🌐 Normes ISO / AFNOR
          </span>
          <div className="mt-1 text-2xl font-bold text-amber-700 dark:text-amber-400">
            {summary?.jurisdiction_breakdown["INTERNATIONAL"] ?? 2}
          </div>
          <p className="mt-0.5 text-[10px] text-amber-600 dark:text-amber-500">ISO 14021 &amp; ISO 14044</p>
        </div>

        <div className="rounded-xl border border-rose-200 bg-rose-50/50 p-4 shadow-sm dark:border-rose-900/50 dark:bg-rose-950/20">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-rose-700 dark:text-rose-400">
            Points d&apos;attention
          </span>
          <div className="mt-1 text-2xl font-bold text-rose-700 dark:text-rose-400">
            {summary?.coverage_warnings_count ?? 3}
          </div>
          <p className="mt-0.5 text-[10px] text-rose-600 dark:text-rose-500">Transposition / Portée</p>
        </div>
      </div>

      {/* Governance & Integrity Statement */}
      <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-2 text-slate-700 dark:text-slate-300">
          <span className="text-emerald-600 font-bold">🔒 Intégrité cryptographique :</span>
          <span className="font-mono bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded text-[11px] text-slate-600 dark:text-slate-400">
            SHA-256 : {summary?.sha256_fingerprint.slice(0, 24)}…
          </span>
        </div>
        <div className="text-[11px] text-slate-500 italic">
          Dernière homologation du Rule Book : {summary?.release_date || "2026-09-24"}
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-white p-3 rounded-xl border border-slate-200 shadow-sm dark:bg-slate-900 dark:border-slate-800">
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setJurisdictionFilter("all")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              jurisdictionFilter === "all"
                ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
            }`}
          >
            Toutes ({rules.length})
          </button>
          <button
            onClick={() => setJurisdictionFilter("FR")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              jurisdictionFilter === "FR"
                ? "bg-blue-600 text-white"
                : "bg-blue-50 text-blue-700 hover:bg-blue-100 dark:bg-blue-950/40 dark:text-blue-300"
            }`}
          >
            🇫🇷 France
          </button>
          <button
            onClick={() => setJurisdictionFilter("EU")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              jurisdictionFilter === "EU"
                ? "bg-indigo-600 text-white"
                : "bg-indigo-50 text-indigo-700 hover:bg-indigo-100 dark:bg-indigo-950/40 dark:text-indigo-300"
            }`}
          >
            🇪🇺 Union Européenne
          </button>
          <button
            onClick={() => setJurisdictionFilter("INTERNATIONAL")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              jurisdictionFilter === "INTERNATIONAL"
                ? "bg-amber-600 text-white"
                : "bg-amber-50 text-amber-700 hover:bg-amber-100 dark:bg-amber-950/40 dark:text-amber-300"
            }`}
          >
            🌐 International / ISO
          </button>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
          >
            <option value="all">-- Toute sévérité --</option>
            <option value="CRITICAL">Critique</option>
            <option value="HIGH">Élevée</option>
            <option value="MEDIUM">Moyenne</option>
          </select>

          <input
            type="text"
            placeholder="Rechercher une règle..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
          >
          </input>
        </div>
      </div>

      {/* Rules Table */}
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
            <tr>
              <th className="py-3 px-4 font-semibold">Identifiant &amp; Intitulé de la règle</th>
              <th className="py-3 px-4 font-semibold">Juridiction &amp; Référence</th>
              <th className="py-3 px-4 font-semibold">Force &amp; Sévérité</th>
              <th className="py-3 px-4 font-semibold">Safe Harbors / Sanction</th>
              <th className="py-3 px-4 font-semibold text-right">Fiche Légale</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
            {rules.map((rule) => (
              <tr key={rule.rule_id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/40 transition">
                <td className="py-3.5 px-4 max-w-sm">
                  <div className="font-mono text-[10px] text-slate-400 font-bold">{rule.rule_id}</div>
                  <p className="font-semibold text-slate-900 dark:text-white text-xs mt-0.5">
                    {rule.title}
                  </p>
                  {rule.incomplete_coverage_warning && (
                    <span className="inline-block mt-1 text-[10px] font-medium text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded border border-amber-200 dark:bg-amber-950/40 dark:border-amber-900 dark:text-amber-300">
                      ⚠️ {rule.incomplete_coverage_warning.slice(0, 60)}…
                    </span>
                  )}
                </td>

                <td className="py-3.5 px-4">
                  <div className="flex items-center gap-1.5">
                    {rule.jurisdiction === "FR" && <span className="font-bold text-blue-600">🇫🇷 FR</span>}
                    {rule.jurisdiction === "EU" && <span className="font-bold text-indigo-600">🇪🇺 UE</span>}
                    {rule.jurisdiction === "INTERNATIONAL" && <span className="font-bold text-amber-600">🌐 INT</span>}
                    <span className="rounded bg-slate-100 px-1.5 py-0.2 text-[10px] font-semibold text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                      {rule.legal_status}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-500 mt-1 line-clamp-2">
                    {rule.legal_reference}
                  </p>
                </td>

                <td className="py-3.5 px-4 whitespace-nowrap">
                  <div className="font-medium text-[11px] text-slate-800 dark:text-slate-200">
                    {rule.legal_force}
                  </div>
                  <span
                    className={`inline-block mt-1 rounded px-2 py-0.5 text-[10px] font-bold ${
                      rule.severity === "CRITICAL"
                        ? "bg-red-100 text-red-800 dark:bg-red-950/60 dark:text-red-300"
                        : rule.severity === "HIGH"
                        ? "bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
                        : "bg-blue-100 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300"
                    }`}
                  >
                    {rule.severity}
                  </span>
                </td>

                <td className="py-3.5 px-4 whitespace-nowrap">
                  <div className="flex flex-col gap-1">
                    {rule.has_safe_harbors ? (
                      <span className="text-[11px] font-semibold text-emerald-700 dark:text-emerald-400">
                        🛡️ Safe Harbor défini
                      </span>
                    ) : (
                      <span className="text-[11px] text-slate-400">Aucun Safe Harbor</span>
                    )}
                    {rule.has_sanctions && (
                      <span className="text-[10px] font-medium text-red-600 dark:text-red-400">
                        ⚖️ Sanction administrative
                      </span>
                    )}
                  </div>
                </td>

                <td className="py-3.5 px-4 text-right whitespace-nowrap">
                  <button
                    onClick={() => setSelectedRuleId(rule.rule_id)}
                    className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700 transition"
                  >
                    Examiner ↗
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Rule Detail Modal */}
      {selectedRuleId && (
        <RuleDetailModal
          ruleId={selectedRuleId}
          isOpen={true}
          canManage={canManage}
          onClose={() => setSelectedRuleId(null)}
          onUpdated={() => loadData()}
        />
      )}

      {/* Changelog Modal */}
      {isChangelogOpen && (
        <RuleDiffModal
          isOpen={true}
          onClose={() => setIsChangelogOpen(false)}
        />
      )}
    </div>
  );
}
