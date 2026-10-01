"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { PublicShell } from "@/components/site/PublicShell";
import { requestUrl } from "@/lib/api";

/**
 * Centre d'aide public (C15) — la page derrière `/aide`.
 *
 * Elle lit `GET /api/v1/public/support`. Ce n'est pas un détail d'implémentation :
 * l'aide contient les **codes d'erreur réels** du produit, un test vérifie que chaque
 * erreur citée existe encore dans le code, et les objectifs de réponse viennent des
 * offres réellement publiées. Recopier ce contenu dans une page statique le ferait
 * diverger sans que personne ne s'en aperçoive.
 *
 * Si l'API est injoignable, la page le dit : elle n'affiche ni objectif de réponse ni
 * liste d'erreurs inventés.
 */

type SupportCentre = {
  contact: { email: string | null; note: string; form_available: boolean };
  start_here: Array<{ title: string; body: string }>;
  frequent_errors: Array<{ error_code: string; title: string; explanation: string; fix: string }>;
  sla: {
    rows: Array<{
      plan_code: string;
      plan_name: string;
      first_response_hours: number;
      incident_response_hours: number;
    }>;
    statement: string;
    exclusions: string[];
  };
  honesty: string;
  help_url: string;
};

export default function HelpPage() {
  const [centre, setCentre] = useState<SupportCentre | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "unreachable">("loading");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(requestUrl("/api/v1/public/support"), {
          credentials: "include",
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = (await response.json()) as SupportCentre;
        if (!cancelled) {
          setCentre(payload);
          setState("ready");
        }
      } catch {
        if (!cancelled) setState("unreachable");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <PublicShell current="legal">
      <main className="mx-auto max-w-4xl px-6 py-14">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
          Centre d&apos;aide
        </p>
        <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">
          Comment démarrer, et quoi faire quand quelque chose bloque
        </h1>
        <p className="mt-4 max-w-3xl text-sm leading-relaxed text-slate-600">
          Les erreurs décrites ici sont celles que le produit renvoie réellement : chaque
          message est vérifié contre le code qui le produit. Les délais de réponse sont des
          objectifs par offre.
        </p>

        {state === "unreachable" && (
          <div className="mt-8 rounded-2xl border border-amber-300 bg-amber-50 p-5 text-sm text-amber-900">
            <p className="font-semibold">Centre d&apos;aide indisponible.</p>
            <p className="mt-2">
              Le serveur ne répond pas : plutôt que d&apos;afficher un contenu recopié qui
              pourrait décrire une autre version du produit, cette page n&apos;affiche rien.
              Réessayez dans un instant, ou passez par le bouton d&apos;aide dans
              l&apos;application.
            </p>
          </div>
        )}

        {state === "loading" && <p className="mt-8 text-sm text-slate-500">Chargement…</p>}

        {centre && (
          <>
            <section className="mt-10 border-t border-slate-200 pt-8">
              <h2 className="text-lg font-extrabold tracking-tight text-forest">
                Contact et délais de réponse
              </h2>
              <p className="mt-3 text-sm text-slate-700">
                {centre.contact.email ? (
                  <>
                    Adresse de support : <strong>{centre.contact.email}</strong>
                  </>
                ) : (
                  <>
                    <strong>Adresse de support non publiée.</strong> {centre.contact.note}
                  </>
                )}
              </p>
              <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white">
                <table className="w-full min-w-[34rem] border-collapse text-left text-xs">
                  <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="px-4 py-3">Offre</th>
                      <th className="px-4 py-3">Première réponse (objectif)</th>
                      <th className="px-4 py-3">Incident (objectif)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {centre.sla.rows.map((row) => (
                      <tr key={row.plan_code}>
                        <td className="px-4 py-3 font-semibold text-slate-800">{row.plan_name}</td>
                        <td className="px-4 py-3 text-slate-600">{row.first_response_hours} h</td>
                        <td className="px-4 py-3 text-slate-600">{row.incident_response_hours} h</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-3 text-xs leading-relaxed text-slate-600">{centre.sla.statement}</p>
              <ul className="mt-3 list-disc space-y-1.5 pl-5 text-xs text-slate-600">
                {centre.sla.exclusions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>

            <section className="mt-10 border-t border-slate-200 pt-8">
              <h2 className="text-lg font-extrabold tracking-tight text-forest">
                Démarrer en quatre étapes
              </h2>
              <ol className="mt-4 space-y-4">
                {centre.start_here.map((item) => (
                  <li key={item.title} className="rounded-xl border border-slate-200 bg-white p-4">
                    <p className="text-sm font-semibold text-forest">{item.title}</p>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">{item.body}</p>
                  </li>
                ))}
              </ol>
            </section>

            <section className="mt-10 border-t border-slate-200 pt-8">
              <h2 className="text-lg font-extrabold tracking-tight text-forest">
                Erreurs fréquentes, et quoi faire
              </h2>
              <div className="mt-4 space-y-3">
                {centre.frequent_errors.map((item) => (
                  <details
                    key={item.error_code}
                    className="rounded-xl border border-slate-200 bg-white p-4"
                  >
                    <summary className="cursor-pointer text-sm font-semibold text-slate-800">
                      {item.title}{" "}
                      <code className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-normal text-slate-500">
                        {item.error_code}
                      </code>
                    </summary>
                    <p className="mt-2 text-sm leading-relaxed text-slate-600">
                      {item.explanation}
                    </p>
                    <p className="mt-2 text-sm leading-relaxed text-forest">
                      <strong>À faire : </strong>
                      {item.fix}
                    </p>
                  </details>
                ))}
              </div>
            </section>

            <p className="mt-8 rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs leading-relaxed text-slate-600">
              {centre.honesty}
            </p>
          </>
        )}

        <div className="mt-12 rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
          Besoin d&apos;un contact direct ?{" "}
          <Link href="/" className="font-semibold text-forest underline">
            Revenir à la présentation du produit
          </Link>
          , ou ouvrez le bouton « Besoin d&apos;aide ? » depuis l&apos;application : il joint
          automatiquement l&apos;écran où vous étiez.
        </div>
      </main>
    </PublicShell>
  );
}
