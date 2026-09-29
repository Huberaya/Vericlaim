"use client";

import type { PersistentClaim } from "@/lib/types";
import { HumanReviewPanel } from "./HumanReviewPanel";

interface HumanReviewModalProps {
  analysisId: string;
  versionNumber: number;
  claims: PersistentClaim[];
  isOpen: boolean;
  onClose: () => void;
  onRefresh?: () => void;
}

export function HumanReviewModal({
  analysisId,
  versionNumber,
  claims,
  isOpen,
  onClose,
  onRefresh,
}: HumanReviewModalProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-5xl rounded-xl bg-white shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 flex flex-col max-h-[92vh]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-800">
          <div>
            <h2 className="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
              <span className="text-indigo-600 dark:text-indigo-400">⚖</span> Arbitrage Humain &amp; Validations (Chantier 6.3)
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Revue contradictoire des allégations, décisions d&apos;homologation juridique et demandes de justificatifs fournisseurs.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          <HumanReviewPanel
            analysisId={analysisId}
            versionNumber={versionNumber}
            claims={claims}
            onRefresh={onRefresh}
          />
        </div>

        {/* Footer */}
        <div className="border-t border-slate-200 bg-slate-50 px-6 py-3 dark:border-slate-800 dark:bg-slate-900/50 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-slate-200 px-4 py-2 text-xs font-semibold text-slate-800 hover:bg-slate-300 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            Fermer
          </button>
        </div>
      </div>
    </div>
  );
}
