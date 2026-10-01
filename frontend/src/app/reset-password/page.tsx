"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { PublicShell } from "@/components/site/PublicShell";
import { confirmPasswordReset, requestPasswordReset } from "@/lib/identity";

/**
 * Password reset, in two faces of the same route:
 *
 * * no `?token=` — ask for a link. The answer is the API's, and it is identical
 *   whether the address exists or not; the screen repeats that wording instead of
 *   promising an e-mail that may never come;
 * * `?token=` — set the new password. Confirming revokes every existing session,
 *   including the one that asked, so the page says so before the visitor submits.
 */
function ResetPanel() {
  // The token is read from `window.location.search` after mount rather than with
  // `useSearchParams`: that hook forces a Suspense boundary during prerendering, so
  // the served HTML contained only "Chargement…" and a visitor without JavaScript
  // could not even ask for a reset link. Measured, then fixed.
  const [token, setToken] = useState("");
  useEffect(() => {
    setToken(new URLSearchParams(window.location.search).get("token") ?? "");
  }, []);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function askForLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const answer = await requestPasswordReset(email);
      setMessage(answer.message);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "La demande a échoué.");
    } finally {
      setBusy(false);
    }
  }

  async function setNewPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const answer = await confirmPasswordReset(token, password);
      setMessage(answer.message);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "La réinitialisation a échoué.");
    } finally {
      setBusy(false);
    }
  }

  if (message) {
    return (
      <>
        <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">Demande traitée</h1>
        <p className="mt-4 text-sm leading-relaxed text-slate-600">{message}</p>
        <Link
          href="/login"
          className="mt-6 inline-block rounded-lg bg-forest px-4 py-2 text-xs font-semibold text-white"
        >
          Aller à la connexion
        </Link>
      </>
    );
  }

  return (
    <>
      <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">
        {token ? "Nouveau mot de passe" : "Mot de passe oublié"}
      </h1>
      {error && (
        <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
          {error}
        </p>
      )}

      {token ? (
        <form onSubmit={setNewPassword} className="mt-8 rounded-2xl border border-slate-200 bg-white p-6">
          <label className="block text-xs font-semibold text-forest" htmlFor="password">
            Nouveau mot de passe
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
            Valider ce formulaire révoque toutes les sessions ouvertes sur ce compte, y compris
            celles d’autres appareils : elles devront se reconnecter.
          </p>
          <button
            type="submit"
            disabled={busy}
            className="mt-5 w-full rounded-lg bg-forest px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-60"
          >
            {busy ? "Enregistrement…" : "Définir le nouveau mot de passe"}
          </button>
        </form>
      ) : (
        <form onSubmit={askForLink} className="mt-8 rounded-2xl border border-slate-200 bg-white p-6">
          <label className="block text-xs font-semibold text-forest" htmlFor="email">
            Adresse e-mail du compte
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={busy}
            className="mt-5 w-full rounded-lg bg-forest px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-60"
          >
            {busy ? "Envoi…" : "Recevoir un lien de réinitialisation"}
          </button>
          <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
            Le lien est valable une heure et ne fonctionne qu’une fois. La réponse est la même que
            l’adresse existe ou non : c’est volontaire, pour ne pas révéler qui possède un compte.
          </p>
        </form>
      )}
    </>
  );
}

export default function ResetPasswordPage() {
  return (
    <PublicShell current="identity">
      <section className="mx-auto max-w-2xl px-6 py-16">
        <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
          Réinitialisation
        </p>
        <ResetPanel />
      </section>
    </PublicShell>
  );
}
