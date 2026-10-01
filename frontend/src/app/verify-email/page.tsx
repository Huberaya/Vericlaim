"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { PublicShell } from "@/components/site/PublicShell";
import { verifyEmail } from "@/lib/identity";

/**
 * E-mail confirmation.
 *
 * The token is read from the query string and spent immediately. It is *not*
 * stored anywhere in the browser: it is one-shot, and a copy left in local storage
 * or a history entry would be a usable credential.
 *
 * `useEffect` is guarded by a ref so React's development double-invocation cannot
 * spend the token twice and turn a success into "déjà utilisé".
 */
function ConfirmationPanel() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<
    | { kind: "working" }
    | { kind: "done"; message: string }
    | { kind: "failed"; message: string }
  >({ kind: "working" });
  const spent = useRef(false);

  useEffect(() => {
    if (spent.current) return;
    spent.current = true;
    if (!token) {
      setState({
        kind: "failed",
        message:
          "Aucun jeton dans le lien. Ouvrez le lien reçu par e-mail tel quel, sans le raccourcir.",
      });
      return;
    }
    verifyEmail(token)
      .then((answer) => setState({ kind: "done", message: answer.message }))
      .catch((cause) =>
        setState({
          kind: "failed",
          message: cause instanceof Error ? cause.message : "La confirmation a échoué.",
        }),
      );
  }, [token]);

  if (state.kind === "working") {
    return <p className="text-sm text-slate-600">Confirmation de votre adresse…</p>;
  }
  if (state.kind === "failed") {
    return (
      <>
        <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">
          Confirmation impossible
        </h1>
        <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {state.message}
        </p>
        <p className="mt-4 text-sm leading-relaxed text-slate-600">
          Un lien de confirmation est valable 24 heures et ne fonctionne qu’une fois. S’il a expiré
          ou a déjà servi, redemandez un lien : la création de compte avec la même adresse en envoie
          un nouveau, sans créer de second compte.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            href="/signup"
            className="rounded-lg bg-forest px-4 py-2 text-xs font-semibold text-white"
          >
            Redemander un lien
          </Link>
          <Link
            href="/reset-password"
            className="rounded-lg border border-slate-300 px-4 py-2 text-xs font-semibold text-forest"
          >
            Redéfinir mon mot de passe
          </Link>
        </div>
      </>
    );
  }
  return (
    <>
      <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">Adresse confirmée</h1>
      <p className="mt-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800" role="status">
        {state.message}
      </p>
      <Link
        href="/login"
        className="mt-6 inline-block rounded-lg bg-forest px-4 py-2 text-xs font-semibold text-white"
      >
        Se connecter
      </Link>
    </>
  );
}

export default function VerifyEmailPage() {
  return (
    <PublicShell current="identity">
      <section className="mx-auto max-w-2xl px-6 py-16">
        <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
          Vérification d’adresse
        </p>
        <Suspense
          fallback={
            <p className="mt-3 text-sm text-slate-600">
              Ouverture du lien de confirmation… Sans JavaScript, ouvrez ce lien depuis un navigateur qui l’autorise :
              l’activation se fait en appelant l’API avec le jeton du lien.
            </p>
          }
        >
          <ConfirmationPanel />
        </Suspense>
      </section>
    </PublicShell>
  );
}
