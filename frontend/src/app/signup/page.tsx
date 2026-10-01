"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { PublicShell } from "@/components/site/PublicShell";
import {
  getPasswordPolicy,
  signupWithPassword,
  type PasswordPolicy,
} from "@/lib/identity";

/**
 * Account creation.
 *
 * The page repeats the answer it received instead of the answer a visitor would
 * like: the API replies identically for a new and an already-known address, and
 * this screen keeps that wording. It never claims an e-mail was delivered — on an
 * instance without a mail transport the message is written to the outbox and not
 * sent, and the browser cannot tell the difference. The copy therefore talks about
 * what to do if nothing arrives.
 */

type Phase = { kind: "form" } | { kind: "submitted"; message: string } | { kind: "error"; message: string };

export default function SignupPage() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>({ kind: "form" });
  const [policy, setPolicy] = useState<PasswordPolicy | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    email: "",
    password: "",
    organizationName: "",
    displayName: "",
  });

  useEffect(() => {
    // The published policy is fetched rather than duplicated in the interface: a
    // client-side copy would drift and start refusing passwords the API accepts.
    getPasswordPolicy()
      .then(setPolicy)
      .catch(() => setPolicy(null));
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      const answer = await signupWithPassword(form);
      setPhase({ kind: "submitted", message: answer.message });
    } catch (cause) {
      setPhase({ kind: "error", message: cause instanceof Error ? cause.message : "La demande a échoué." });
    } finally {
      setBusy(false);
    }
  }

  if (phase.kind === "submitted") {
    return (
      <PublicShell current="identity">
        <section className="mx-auto max-w-2xl px-6 py-16">
          <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
            Demande enregistrée
          </p>
          <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">
            Vérifiez votre boîte de réception
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-slate-600">{phase.message}</p>
          <ul className="mt-6 space-y-2 text-sm text-slate-600">
            <li>
              · Le lien de confirmation est valable 24 heures et ne fonctionne qu’une fois.
            </li>
            <li>
              · Rien reçu après quelques minutes ? Vérifiez les indésirables, puis réessayez la
              création de compte : une seconde demande envoie un lien de réinitialisation plutôt
              qu’un doublon de compte.
            </li>
          </ul>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/login"
              className="rounded-lg border border-slate-300 px-4 py-2 text-xs font-semibold text-forest hover:bg-slate-50"
            >
              Se connecter
            </Link>
            <Link
              href="/reset-password"
              className="rounded-lg border border-slate-300 px-4 py-2 text-xs font-semibold text-forest hover:bg-slate-50"
            >
              Redéfinir mon mot de passe
            </Link>
          </div>
        </section>
      </PublicShell>
    );
  }

  return (
    <PublicShell current="identity">
      <section className="mx-auto grid max-w-5xl gap-10 px-6 py-14 md:grid-cols-[1.1fr_1fr]">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
            Créer un compte
          </p>
          <h1 className="mt-3 text-4xl font-extrabold tracking-tight text-forest">
            Votre espace, sans passer par nous.
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-slate-600">
            La création de compte, la confirmation d’adresse et l’invitation de vos collègues sont
            automatisées : aucune intervention de l’éditeur n’est nécessaire.
          </p>
          <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-5">
            <p className="text-xs font-bold text-forest">Règles du mot de passe</p>
            {policy ? (
              <ul className="mt-2 space-y-1 text-xs text-slate-600">
                <li>· Au moins {policy.min_length} caractères, au plus {policy.max_length}.</li>
                <li>· Au moins un chiffre et une lettre.</li>
                <li>· Ni votre adresse e-mail, ni votre nom d’utilisateur.</li>
                <li>
                  · Stockage {policy.hashing.scheme} ({policy.hashing.note}).
                </li>
              </ul>
            ) : (
              <p className="mt-2 text-xs text-slate-500">
                Politique indisponible : l’API ne répond pas. Le formulaire reste soumis aux mêmes
                règles et un refus affichera le détail.
              </p>
            )}
          </div>
        </div>

        <form onSubmit={onSubmit} className="rounded-2xl border border-slate-200 bg-white p-6">
          {phase.kind === "error" && (
            <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
              {phase.message}
            </p>
          )}
          <label className="block text-xs font-semibold text-forest" htmlFor="email">
            Adresse e-mail professionnelle
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={form.email}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />

          <label className="mt-4 block text-xs font-semibold text-forest" htmlFor="organization_name">
            Nom de votre organisation
          </label>
          <input
            id="organization_name"
            required
            minLength={2}
            value={form.organizationName}
            onChange={(event) => setForm({ ...form, organizationName: event.target.value })}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />

          <label className="mt-4 block text-xs font-semibold text-forest" htmlFor="display_name">
            Votre nom (facultatif)
          </label>
          <input
            id="display_name"
            value={form.displayName}
            onChange={(event) => setForm({ ...form, displayName: event.target.value })}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />

          <label className="mt-4 block text-xs font-semibold text-forest" htmlFor="password">
            Mot de passe
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete="new-password"
            value={form.password}
            onChange={(event) => setForm({ ...form, password: event.target.value })}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />

          <button
            type="submit"
            disabled={busy}
            className="mt-6 w-full rounded-lg bg-forest px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-60"
          >
            {busy ? "Création…" : "Créer mon compte"}
          </button>
          <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
            En créant un compte vous acceptez les conditions d’utilisation et la politique de
            confidentialité, qui restent à valider par un conseil juridique (voir la page légale).
          </p>
          <button
            type="button"
            onClick={() => router.push("/login")}
            className="mt-3 w-full text-center text-[11px] font-semibold text-forest underline"
          >
            J’ai déjà un compte
          </button>
        </form>
      </section>
    </PublicShell>
  );
}
