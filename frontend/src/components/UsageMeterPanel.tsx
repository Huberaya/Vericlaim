"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getBillingSubscription, getBillingUsage } from "@/lib/api";
import type { BillingSubscription, BillingUsage } from "@/lib/types";

/**
 * C13 — compteurs d'usage et état de l'offre, dans l'application.
 *
 * Ce panneau ne calcule **rien** : il affiche ce que l'API applique réellement
 * (`GET /api/v1/billing/usage` et `GET /api/v1/billing/subscription`). Trois
 * conséquences voulues :
 *
 * 1. Un quota atteint n'est pas d'abord découvert par un refus d'import : il se
 *    lit ici, avec la limite exacte et ce qu'il reste ;
 * 2. les lignes `counted: false` sont affichées telles quelles — une jauge de
 *    sièges n'est pas un compteur qui « se consomme », la présenter comme un
 *    quota consommable serait faux ;
 * 3. le `note` de l'API est rendu **tel quel**, y compris ce qu'il dit de
 *    désagréable (un import refusé pour quota consomme tout de même un document).
 *
 * Aucun bouton d'achat n'est monté ici : le produit n'a pas d'encaissement
 * configuré, et un bouton qui ne mène à rien est un mensonge d'interface. Les
 * actions possibles sont **annoncées** (`available_actions`), pas simulées.
 */

function formatMoment(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("fr-FR", { dateStyle: "long", timeZone: "Europe/Paris" }).format(
    parsed,
  );
}

const STATUS_LABELS: Record<string, string> = {
  trialing: "Essai en cours",
  active: "Offre active",
  past_due: "Paiement en retard",
  canceled: "Résiliée — accès jusqu'au terme payé",
  none: "Aucune offre en cours",
};

export function UsageMeterPanel() {
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [subscription, setSubscription] = useState<BillingSubscription | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState("loading");
    setError(null);
    try {
      const [usagePayload, subscriptionPayload] = await Promise.all([
        getBillingUsage(),
        getBillingSubscription(),
      ]);
      setUsage(usagePayload);
      setSubscription(subscriptionPayload);
      setState("ready");
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "La consommation n'a pas pu être lue auprès du serveur.",
      );
      setState("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state === "loading") {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500">
        Lecture de la consommation du mois en cours…
      </section>
    );
  }

  if (state === "error" || usage === null) {
    return (
      <section className="rounded-xl border border-rose-300 bg-rose-50 p-5 text-sm text-rose-900">
        <strong>Consommation indisponible.</strong> {error} Aucun chiffre n&apos;est affiché à la
        place : un compteur inventé vaut moins qu&apos;un compteur absent.{" "}
        <button type="button" className="underline" onClick={() => void load()}>
          Réessayer
        </button>
      </section>
    );
  }

  const planLabel = subscription?.plan_name ?? (usage.plan_code ? usage.plan_code : "aucune offre");

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500">
            Consommation de la période
          </h3>
          <p className="mt-1 text-sm text-slate-700">
            Offre appliquée : <strong>{planLabel}</strong>
            {subscription && ` · ${STATUS_LABELS[subscription.status] ?? subscription.status}`}
          </p>
        </div>
        <button
          type="button"
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-forest"
          onClick={() => void load()}
        >
          Actualiser
        </button>
      </header>

      {subscription?.reason && (
        <p className="mt-2 text-xs leading-relaxed text-slate-500">{subscription.reason}</p>
      )}

      <p className="mt-1 text-xs text-slate-500">
        Période du {formatMoment(usage.period_start)} au {formatMoment(usage.period_end)} ·
        remise à zéro le {formatMoment(usage.resets_at)}
      </p>

      <div className="mt-4 space-y-3">
        {usage.lines.map((line) => {
          const ratio =
            line.limit && line.limit > 0 ? Math.min(100, Math.round((line.used / line.limit) * 100)) : 0;
          return (
            <div key={line.metric}>
              <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
                <span className="font-semibold text-slate-700">{line.label}</span>
                <span className={line.exceeded ? "font-bold text-rose-700" : "text-slate-600"}>
                  {line.limit === null
                    ? `${line.used} — aucune limite publiée pour cette offre`
                    : `${line.used} / ${line.limit}`}
                  {line.limit !== null && (
                    <>
                      {" · "}
                      {line.remaining !== null && line.remaining > 0
                        ? `reste ${line.remaining}`
                        : "limite atteinte"}
                    </>
                  )}
                  {!line.counted && " · jauge, non consommable"}
                </span>
              </div>
              {line.limit !== null && (
                <div className="mt-1 h-1.5 w-full rounded-full bg-slate-100">
                  <div
                    className={`h-1.5 rounded-full ${line.exceeded ? "bg-rose-500" : "bg-forest"}`}
                    style={{ width: `${line.exceeded ? 100 : ratio}%` }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-slate-500">{usage.note}</p>

      {subscription && (
        <div className="mt-3 space-y-1 text-[11px] leading-relaxed text-slate-600">
          {subscription.cancel_at_period_end && (
            <p>
              <strong>Résiliation programmée</strong> : l&apos;accès reste ouvert jusqu&apos;au{" "}
              {formatMoment(subscription.current_period_end)}.
            </p>
          )}
          {subscription.pending_plan_code && (
            <p>
              <strong>Changement d&apos;offre programmé</strong> vers « {subscription.pending_plan_code} » le{" "}
              {formatMoment(subscription.pending_plan_effective_at)}. Une baisse d&apos;offre prend
              effet à la fin de la période payée, jamais immédiatement.
            </p>
          )}
          {subscription.available_actions.length > 0 && (
            <p>Actions possibles sur cette offre : {subscription.available_actions.join(", ")}.</p>
          )}
        </div>
      )}

      <p className="mt-3 text-[11px] text-slate-500">
        Grille et plafonds détaillés :{" "}
        <Link className="underline" href="/pricing">
          tarifs et quotas
        </Link>
        .
      </p>
    </section>
  );
}
