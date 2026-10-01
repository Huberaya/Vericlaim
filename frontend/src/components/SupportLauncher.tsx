"use client";

import { useEffect, useMemo, useState } from "react";
import { requestUrl } from "@/lib/api";

/**
 * C15 — bouton de contact contextualisé, dans l'application.
 *
 * Trois propriétés, et elles sont la raison d'être du composant :
 *
 * 1. **Le contexte part avec la demande.** L'écran affiché et le dernier code d'erreur
 *    rencontré sont joints automatiquement : le support reçoit de quoi reproduire, pas
 *    une description approximative. Un incident qu'on ne peut pas reproduire coûte un
 *    aller-retour.
 * 2. **Le délai affiché est celui du plan, pas un argument commercial.** Il vient de
 *    `GET /api/v1/public/support` (objectifs publiés), et il est écrit « objectif
 *    indicatif » — parce que rien ne mesure aujourd'hui le temps de réponse réel.
 * 3. **Sans serveur, il ne fait pas semblant.** Si les objectifs ne se chargent pas, le
 *    bouton le dit et propose le centre d'aide au lieu d'afficher un délai inventé.
 */

type SlaRow = {
  plan_code: string;
  plan_name: string;
  first_response_hours: number;
  incident_response_hours: number;
};

type SupportCentre = {
  contact: { email: string | null; note: string };
  sla: { rows: SlaRow[]; statement: string; exclusions: string[] };
  help_url: string;
};

type SupportRequestResponse = {
  id: string;
  status: string;
  first_response_hours: number;
  first_response_due_at: string;
};

const CATEGORIES: Array<{ value: string; label: string }> = [
  { value: "question", label: "Question sur le fonctionnement" },
  { value: "incident", label: "Quelque chose ne fonctionne pas" },
  { value: "extraction", label: "Document, OCR ou analyse" },
  { value: "report", label: "Rapport signé ou téléchargement" },
  { value: "billing", label: "Offre, quota ou facture" },
  { value: "privacy", label: "Droits des personnes" },
  { value: "other", label: "Autre" },
];

export function SupportLauncher({
  /** Écran courant de l'application (« audit », « catalogue », « rapport »…). */
  screen,
  /** Dernier code d'erreur affiché, s'il y en a un : il part avec la demande. */
  lastErrorCode,
  planCode,
}: {
  screen: string;
  lastErrorCode?: string | null;
  planCode?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [centre, setCentre] = useState<SupportCentre | null>(null);
  const [category, setCategory] = useState("question");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [created, setCreated] = useState<SupportRequestResponse | null>(null);
  // Le plan applicable vient du serveur (`/api/v1/billing/subscription`) : le client ne
  // choisit pas l'objectif de réponse auquel il a droit, il le lit.
  const [plan, setPlan] = useState<string | null>(planCode ?? null);

  useEffect(() => {
    if (!open || centre) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(requestUrl("/api/v1/public/support"), {
          credentials: "include",
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = (await response.json()) as SupportCentre;
        if (!cancelled) setCentre(payload);
      } catch {
        if (!cancelled) setCentre(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, centre]);

  useEffect(() => {
    if (!open || plan) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(requestUrl("/api/v1/billing/subscription"), {
          credentials: "include",
          cache: "no-store",
        });
        if (!response.ok) return;
        const payload = (await response.json()) as { plan_code?: string | null };
        if (!cancelled && payload.plan_code) setPlan(payload.plan_code);
      } catch {
        // Sans plan connu, l'objectif de réponse reste indiqué comme non publié : c'est
        // le comportement voulu, pas une dégradation silencieuse.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, plan]);

  const target = useMemo(() => {
    if (!centre) return null;
    const row = centre.sla.rows.find((item) => item.plan_code === plan);
    if (!row) return null;
    return category === "incident" ? row.incident_response_hours : row.first_response_hours;
  }, [centre, plan, category]);

  async function submit() {
    setState("sending");
    setFeedback(null);
    try {
      const response = await fetch(requestUrl("/api/v1/support/requests"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(document.cookie.includes("vericlaim_csrf")
            ? {
                "X-CSRF-Token":
                  document.cookie
                    .split("; ")
                    .find((item) => item.startsWith("vericlaim_csrf="))
                    ?.split("=")[1] ?? "",
              }
            : {}),
        },
        body: JSON.stringify({
          category,
          subject,
          message,
          screen,
          last_error_code: lastErrorCode ?? null,
        }),
        credentials: "include",
        cache: "no-store",
      });
      const payload = (await response.json()) as SupportRequestResponse & {
        detail?: { message?: string };
      };
      if (!response.ok) {
        setState("error");
        setFeedback(payload.detail?.message ?? `Envoi refusé (HTTP ${response.status}).`);
        return;
      }
      setCreated(payload);
      setState("sent");
      setSubject("");
      setMessage("");
    } catch {
      setState("error");
      setFeedback("Serveur injoignable : la demande n'a pas été enregistrée, rien n'est perdu.");
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="fixed bottom-6 right-6 z-40 rounded-full bg-forest px-4 py-3 text-sm font-semibold text-lime shadow-lg transition hover:brightness-110"
        aria-expanded={open}
        aria-controls="support-panel"
      >
        {open ? "Fermer" : "Besoin d'aide ?"}
      </button>

      {open && (
        <section
          id="support-panel"
          aria-label="Support"
          className="fixed bottom-24 right-6 z-40 max-h-[70vh] w-[min(26rem,calc(100vw-3rem))] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl"
        >
          <h2 className="text-sm font-extrabold tracking-tight text-forest">Support VeriClaim</h2>

          {centre ? (
            <p className="mt-1 text-xs text-slate-600">
              Objectif de première réponse :{" "}
              <strong>{target ?? "non publié pour votre offre"}</strong>
              {target ? " h" : ""} — objectif indicatif, non contractuel.
            </p>
          ) : (
            <p className="mt-1 text-xs text-amber-700">
              Objectifs de réponse indisponibles (serveur injoignable) : aucun délai n&apos;est
              affiché à leur place.
            </p>
          )}

          <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 font-mono text-[11px] text-slate-600">
            écran joint : {screen}
            {lastErrorCode ? ` · dernier code d'erreur : ${lastErrorCode}` : ""}
          </p>

          {state === "sent" && created ? (
            <div className="mt-3 rounded-xl border border-forest/20 bg-forest/5 p-3 text-xs text-forest">
              <p className="font-semibold">Demande enregistrée ({created.id.slice(0, 8)}…).</p>
              <p className="mt-1">
                Réponse attendue avant le{" "}
                {new Date(created.first_response_due_at).toLocaleString("fr-FR")} — objectif de{" "}
                {created.first_response_hours} h. Vous recevrez la réponse à l&apos;adresse de votre
                compte.
              </p>
            </div>
          ) : (
            <form
              className="mt-3 space-y-2"
              onSubmit={(event) => {
                event.preventDefault();
                void submit();
              }}
            >
              <label className="block text-xs font-semibold text-slate-700">
                Sujet de la demande
                <select
                  className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs font-normal"
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                >
                  {CATEGORIES.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-xs font-semibold text-slate-700">
                Objet
                <input
                  className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs font-normal"
                  value={subject}
                  minLength={3}
                  maxLength={255}
                  required
                  onChange={(event) => setSubject(event.target.value)}
                  placeholder="En une ligne : ce qui ne va pas ou ce que vous cherchez"
                />
              </label>
              <label className="block text-xs font-semibold text-slate-700">
                Message
                <textarea
                  className="mt-1 h-24 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs font-normal"
                  value={message}
                  minLength={20}
                  maxLength={8000}
                  required
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder="Ce que vous faisiez, ce que vous attendiez, ce que vous avez obtenu."
                />
              </label>
              <button
                type="submit"
                disabled={state === "sending"}
                className="w-full rounded-lg bg-forest px-3 py-2 text-xs font-semibold text-lime disabled:opacity-50"
              >
                {state === "sending" ? "Envoi…" : "Envoyer la demande"}
              </button>
              {feedback && (
                <p className="text-xs text-amber-700" role="alert">
                  {feedback}
                </p>
              )}
              <p className="text-[11px] leading-relaxed text-slate-500">
                Les délais affichés sont des objectifs indicatifs ; la disponibilité du service
                n&apos;est pas mesurée.{" "}
                <a className="underline" href={centre?.help_url ?? "/aide"}>
                  Centre d&apos;aide
                </a>
                {centre?.contact.email ? <> · {centre.contact.email}</> : null}
              </p>
            </form>
          )}
        </section>
      )}
    </>
  );
}

export default SupportLauncher;
