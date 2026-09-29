"use client";

import { useEffect, useState } from "react";
import {
  getAuditIntegrityCertificate,
  listAuditEvents,
  verifyAuditChain,
} from "@/lib/api";
import type {
  AuditChainVerification,
  AuditEventLog,
  AuditIntegrityCertificate,
} from "@/lib/types";

export function AuditTrailExplorer() {
  const [events, setEvents] = useState<AuditEventLog[]>([]);
  const [verification, setVerification] = useState<AuditChainVerification | null>(null);
  const [certificate, setCertificate] = useState<AuditIntegrityCertificate | null>(null);
  const [isCertModalOpen, setIsCertModalOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isVerifying, setIsVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [filterEntity, setFilterEntity] = useState<string>("");
  const [filterAction, setFilterAction] = useState<string>("");

  async function loadData() {
    setIsLoading(true);
    setError(null);
    try {
      const [eventsRes, verifyRes] = await Promise.all([
        listAuditEvents({
          entity_type: filterEntity || undefined,
          action: filterAction || undefined,
          limit: 50,
        }),
        verifyAuditChain(),
      ]);
      setEvents(eventsRes);
      setVerification(verifyRes);
    } catch (err: unknown) {
      console.error("Failed to load audit events:", err);
      setError(err instanceof Error ? err.message : "Erreur de chargement du registre d'audit");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, [filterEntity, filterAction]);

  async function handleVerifyChain() {
    setIsVerifying(true);
    try {
      const result = await verifyAuditChain();
      setVerification(result);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur lors de la vérification");
    } finally {
      setIsVerifying(false);
    }
  }

  async function handleOpenCertificate() {
    try {
      const cert = await getAuditIntegrityCertificate();
      setCertificate(cert);
      setIsCertModalOpen(true);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur lors de l'obtention du certificat");
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
            <span className="text-indigo-600 dark:text-indigo-400">🛡️</span>
            Journal d&apos;Audit Immuable &amp; Scellement Cryptographique (Chantier 11)
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Piste d&apos;audit append-only scellée par hash-chaining SHA-256 avec isolation PostgreSQL RLS par organisation.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={isVerifying}
            onClick={handleVerifyChain}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-1.5 text-xs font-semibold text-slate-700 dark:text-slate-200 shadow-sm hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50 transition"
          >
            <span>🔍</span> {isVerifying ? "Vérification..." : "Vérifier la chaîne SHA-256"}
          </button>

          <button
            type="button"
            onClick={handleOpenCertificate}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 transition"
          >
            <span>📜</span> Certificat d&apos;Intégrité
          </button>
        </div>
      </div>

      {/* Verification Status Card */}
      {verification && (
        <div
          className={`rounded-xl border p-4 shadow-sm flex flex-wrap items-center justify-between gap-4 text-xs ${
            verification.is_valid
              ? "border-emerald-200 bg-emerald-50/60 dark:border-emerald-900/60 dark:bg-emerald-950/30"
              : "border-rose-300 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/40"
          }`}
        >
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              {verification.is_valid ? (
                <>
                  <span className="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse" />
                  <span className="font-bold text-emerald-900 dark:text-emerald-200 uppercase tracking-wide">
                    Chaîne d&apos;Audit Scellée &amp; Mathématiquement Intègre
                  </span>
                </>
              ) : (
                <>
                  <span className="h-2.5 w-2.5 rounded-full bg-rose-500" />
                  <span className="font-bold text-rose-900 dark:text-rose-200 uppercase tracking-wide">
                    Altération Détectée dans la Chaîne de Scellement
                  </span>
                </>
              )}
            </div>
            <p className="text-slate-600 dark:text-slate-300 text-[11px]">
              {verification.is_valid
                ? `L'intégralité des ${verification.total_events} événements enregistrés respecte la chaîne de hachage sans rupture ni falsification.`
                : verification.error_detail}
            </p>
          </div>

          <div className="flex items-center gap-4 text-[11px] text-slate-500 dark:text-slate-400 font-mono">
            {verification.head_event_hash && (
              <div>
                <span className="text-[10px] uppercase font-sans text-slate-400 block">HEAD SHA-256 :</span>
                <span>{verification.head_event_hash.slice(0, 16)}…</span>
              </div>
            )}
            <div>
              <span className="text-[10px] uppercase font-sans text-slate-400 block">Dernier contrôle :</span>
              <span>{new Date(verification.verified_at).toLocaleTimeString("fr-FR")}</span>
            </div>
          </div>
        </div>
      )}

      {/* Filter Toolbar */}
      <div className="flex flex-wrap items-center gap-3 bg-slate-50 dark:bg-slate-850 p-3 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
        <label className="flex items-center gap-2">
          <span className="font-semibold text-slate-600 dark:text-slate-400">Entité :</span>
          <select
            value={filterEntity}
            onChange={(e) => setFilterEntity(e.target.value)}
            className="rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs text-slate-800 dark:text-white"
          >
            <option value="">Toutes les entités</option>
            <option value="analysis">Analyses (analysis)</option>
            <option value="claim">Allégations (claim)</option>
            <option value="document">Documents (document)</option>
            <option value="evidence">Preuves (evidence)</option>
            <option value="validation">Validations humaines</option>
            <option value="apikey">Clés d&apos;API</option>
            <option value="legal_hold">Legal Holds</option>
          </select>
        </label>

        <label className="flex items-center gap-2">
          <span className="font-semibold text-slate-600 dark:text-slate-400">Action :</span>
          <input
            type="text"
            placeholder="ex. created, approved..."
            value={filterAction}
            onChange={(e) => setFilterAction(e.target.value)}
            className="rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs text-slate-800 dark:text-white"
          />
        </label>

        <button
          onClick={() => {
            setFilterEntity("");
            setFilterAction("");
          }}
          className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline ml-auto"
        >
          Réinitialiser filtres
        </button>
      </div>

      {/* Events Table */}
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
            <tr>
              <th className="py-3 px-4 font-semibold">Horodatage UTC</th>
              <th className="py-3 px-4 font-semibold">Entité &amp; Action</th>
              <th className="py-3 px-4 font-semibold">Payload Métier</th>
              <th className="py-3 px-4 font-semibold">Chaîne Cryptographique (Previous ➔ Event Hash)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
            {events.map((ev) => (
              <tr key={ev.id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/40">
                <td className="py-3 px-4 font-mono text-[11px] text-slate-500 whitespace-nowrap">
                  {new Date(ev.occurred_at).toLocaleString("fr-FR")}
                </td>
                <td className="py-3 px-4 whitespace-nowrap">
                  <div className="flex items-center gap-1.5">
                    <span className="rounded bg-indigo-100 px-2 py-0.5 text-[10px] font-bold text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300">
                      {ev.entity_type}
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-mono text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                      {ev.action}
                    </span>
                  </div>
                </td>
                <td className="py-3 px-4 font-mono text-[11px] text-slate-700 dark:text-slate-300 max-w-xs truncate">
                  {JSON.stringify(ev.payload_json)}
                </td>
                <td className="py-3 px-4 font-mono text-[10px] text-slate-500 space-y-0.5 whitespace-nowrap">
                  <div>
                    <span className="text-slate-400">prev: </span>
                    <span>{ev.previous_event_hash ? `${ev.previous_event_hash.slice(0, 12)}…` : "GENESIS_EMPTY"}</span>
                  </div>
                  <div className="text-indigo-600 dark:text-indigo-400 font-bold">
                    <span className="text-slate-400">hash: </span>
                    <span>{ev.event_hash.slice(0, 16)}…</span>
                  </div>
                </td>
              </tr>
            ))}
            {events.length === 0 && (
              <tr>
                <td colSpan={4} className="py-8 text-center text-slate-400">
                  Aucun événement d&apos;audit trouvé pour les filtres sélectionnés.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Certificate Modal */}
      {isCertModalOpen && certificate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto">
          <div className="relative w-full max-w-lg rounded-xl bg-white shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 p-6 space-y-4 text-xs">
            <div className="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-slate-800">
              <h3 className="text-base font-bold text-slate-900 dark:text-white flex items-center gap-2">
                <span>📜</span> Certificat d&apos;Intégrité Cryptographique
              </h3>
              <button
                onClick={() => setIsCertModalOpen(false)}
                className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
              >
                ✕
              </button>
            </div>

            <div className="space-y-3 font-mono text-[11px] bg-slate-50 p-4 rounded-lg border border-slate-200 dark:bg-slate-800 dark:border-slate-700 text-slate-800 dark:text-slate-200">
              <div><strong>N° CERTIFICAT :</strong> {certificate.certificate_id}</div>
              <div><strong>ORGANISATION :</strong> {certificate.organization_name}</div>
              <div><strong>LONGUEUR DE CHAÎNE :</strong> {certificate.chain_length} événements</div>
              <div><strong>STATUT :</strong> <span className="text-emerald-600 font-bold">{certificate.verification_status}</span></div>
              <div><strong>HEAD SHA-256 :</strong> {certificate.head_event_hash}</div>
              <div><strong>CONDENSAT MERKLE :</strong> {certificate.merkle_digest}</div>
              <div><strong>ÉMIS LE :</strong> {new Date(certificate.certified_at).toLocaleString("fr-FR")}</div>
              <div><strong>AUTORITÉ :</strong> {certificate.issuer}</div>
            </div>

            <p className="text-[10px] text-slate-500 italic leading-relaxed">
              {certificate.legal_disclaimer}
            </p>

            <div className="flex justify-end pt-2">
              <button
                onClick={() => setIsCertModalOpen(false)}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700"
              >
                Fermer
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
