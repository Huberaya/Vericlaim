"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { PublicShell } from "@/components/site/PublicShell";
import { passwordLogin } from "@/lib/identity";

/**
 * Password login — the SME fallback next to the enterprise SSO.
 *
 * On success the API has already set the session cookie, so the page only
 * navigates to the application. It does not decide by itself whether the visitor
 * is authenticated: `/app` re-checks the session server-side.
 */
export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await passwordLogin(email, password);
      router.push("/app");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "La connexion a échoué.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PublicShell current="identity">
      <section className="mx-auto max-w-md px-6 py-16">
        <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">Connexion</p>
        <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">
          Accéder à votre espace
        </h1>

        <form onSubmit={onSubmit} className="mt-8 rounded-2xl border border-slate-200 bg-white p-6">
          {error && (
            <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
              {error}
            </p>
          )}
          <label className="block text-xs font-semibold text-forest" htmlFor="email">
            Adresse e-mail
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
          <label className="mt-4 block text-xs font-semibold text-forest" htmlFor="password">
            Mot de passe
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={busy}
            className="mt-6 w-full rounded-lg bg-forest px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-60"
          >
            {busy ? "Connexion…" : "Se connecter"}
          </button>
          <div className="mt-4 flex justify-between text-[11px] font-semibold text-forest">
            <Link className="underline" href="/reset-password">
              Mot de passe oublié
            </Link>
            <Link className="underline" href="/signup">
              Créer un compte
            </Link>
          </div>
        </form>

        <p className="mt-5 text-[11px] leading-relaxed text-slate-500">
          Après cinq tentatives infructueuses, l’adresse est temporairement verrouillée. Le
          verrouillage s’applique aussi au bon mot de passe : c’est ce qui empêche un essai
          automatisé de deviner un mot de passe.
        </p>
      </section>
    </PublicShell>
  );
}
