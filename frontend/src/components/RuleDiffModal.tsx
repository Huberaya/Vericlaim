"use client";

import { useEffect, useState } from "react";
import { getRuleBookChangelog } from "@/lib/api";
import type { RuleBookChangelogEntry } from "@/lib/types";

interface RuleDiffModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function RuleDiffModal({ isOpen, onClose }: RuleDiffModalProps) {
  const [changelog, setChangelog] = useState<RuleBookChangelogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    async function loadChangelog() {
      setIsLoading(true);
      setError(null);
      try {
        const data = await getRuleBookChangelog();
        setChangelog(data);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Erreur de chargement de l'historique");
      } finally {
        setIsLoading(false);
      }
    }
    loadChangelog();
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-3xl rounded-xl bg-white shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 flex flex-col max-h-[88vh]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-800">
          <div>
            <h2 className="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
              <span>📜</span> Historique des Révisions du Référentiel Réglementaire
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Traçabilité des évolutions de règles, modifications de seuils et dates d&apos;application.
            </p>
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
            <div className="py-8 text-center text-slate-400">
              Chargement de l&apos;historique...
            </div>
          )}

          <div className="space-y-6">
            {changelog.map((entry) => (
              <div
                key={entry.version}
                className="rounded-xl border border-slate-200 bg-slate-50/50 p-5 dark:border-slate-800 dark:bg-slate-850 space-y-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-2 dark:border-slate-700">
                  <div>
                    <span className="font-mono text-xs font-bold text-indigo-600 dark:text-indigo-400">
                      Édition {entry.version}
                    </span>
                    <h3 className="font-bold text-slate-900 dark:text-white text-sm mt-0.5">
                      {entry.title}
                    </h3>
                  </div>
                  <span className="rounded bg-slate-200 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                    📅 {new Date(entry.release_date).toLocaleDateString("fr-FR")}
                  </span>
                </div>

                <p className="text-slate-600 dark:text-slate-300 leading-relaxed">
                  {entry.description}
                </p>

                {/* Diff Items */}
                <div className="space-y-2 pt-1">
                  <h4 className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    Modifications &amp; Différentiel ({entry.diff_items.length})
                  </h4>
                  <div className="space-y-2">
                    {entry.diff_items.map((diff, idx) => (
                      <div
                        key={idx}
                        className="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900 space-y-1.5"
                      >
                        <div className="flex items-center gap-2">
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                              diff.diff_type === "added"
                                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                                : diff.diff_type === "modified"
                                ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                                : "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300"
                            }`}
                          >
                            {diff.diff_type}
                          </span>
                          <span className="font-mono text-[11px] font-semibold text-slate-800 dark:text-slate-200">
                            {diff.rule_id}
                          </span>
                        </div>
                        <p className="font-medium text-slate-800 dark:text-slate-200 text-xs">
                          {diff.title}
                        </p>
                        <p className="text-[11px] text-slate-500">{diff.summary}</p>

                        {/* Field level diffs */}
                        {diff.field_diffs && diff.field_diffs.length > 0 && (
                          <div className="rounded bg-slate-50 p-2 text-[10px] font-mono dark:bg-slate-800 space-y-0.5">
                            {diff.field_diffs.map((fd, fIdx) => (
                              <div key={fIdx} className="flex gap-2">
                                <span className="text-slate-500 font-bold">{fd.field} :</span>
                                <span className="text-rose-600 line-through">{String(fd.old_value)}</span>
                                <span className="text-emerald-600 font-semibold">→ {String(fd.new_value)}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
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
