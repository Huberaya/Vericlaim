"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { PublicShell } from "@/components/site/PublicShell";
import { acceptInvitation } from "@/lib/identity";

/**
 * Invitation acceptance.
 *
 * The colleague arrives from the message the product recorded; this page spends the
 * one-shot token, sets their password and opens a session. The token is never
 * written to storage — it lives in the URL the mail contained and dies on use.
 */
function AcceptPanel() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await acceptInvitation(token, password, displayName);
      // The API has already set the session cookie; /app re-checks it server-side.
      router.push("/app");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "L’invitation n’a pas pu être acceptée.");
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <>
        <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">
          Lien d’invitation incomplet
        </h1>
        <p className="mt-4 text-sm leading-relaxed text-slate-600">
          Ouvrez le lien reçu par e-mail tel quel : le jeton d’invitation est indispensable. Si vous
          l’avez perdu, demandez à la personne qui vous a invité de réémettre l’invitation.
        </p>
        <Link
          href="/login"
          className="mt-6 inline-block rounded-lg border border-slate-300 px-4 py-2 text-xs font-semibold text-forest"
        >
          J’ai déjà un mot de passe
        </Link>
      </>
    );
  }

  return (
    <>
      <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">
        Rejoindre l’espace de votre équipe
      </h1>
      <p className="mt-3 text-sm leading-relaxed text-slate-600">
        Choisissez votre mot de passe : il deviendra votre moyen de connexion, en plus du SSO si
        votre organisation l’utilise.
      </p>
      <form onSubmit={onSubmit} className="mt-8 rounded-2xl border border-slate-200 bg-white p-6">
        {error && (
          <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
            {error}
          </p>
        )}
        <label className="block text-xs font-semibold text-forest" htmlFor="display_name">
          Votre nom (facultatif)
        </label>
        <input
          id="display_name"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
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
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={busy}
          className="mt-6 w-full rounded-lg bg-forest px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-60"
        >
          {busy ? "Activation…" : "Activer mon accès"}
        </button>
        <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
          Le lien ne fonctionne qu’une fois et expire au bout de 7 jours.
        </p>
      </form>
    </>
  );
}

export default function InvitationPage() {
  return (
    <PublicShell current="identity">
      <section className="mx-auto max-w-2xl px-6 py-16">
        <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
          Invitation
        </p>
        <Suspense
          fallback={
            <p className="mt-3 text-sm text-slate-600">
              Ouverture de votre invitation… Sans JavaScript, ouvrez ce lien depuis un navigateur qui l’autorise :
              l’activation se fait en appelant l’API avec le jeton du lien.
            </p>
          }
        >
          <AcceptPanel />
        </Suspense>
      </section>
    </PublicShell>
  );
}
