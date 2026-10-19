# VeriClaim A — registre d'exécution

## Référence et préservation

Départ : `36cd8313434a3b51a2428e676d5e5d8a851ee8e2`, également main distant vérifié par fetch. Arbre initial propre. Branche autorisée unique : `arena/b48349e4-vericlaim`. Aucun sous-agent disponible ; les périmètres A/B sont exécutés par le responsable.

## Réconciliation

- PR #9 : head `8cf78a3cc9c9eef66f5d1d200df981e8c391528b`, contient main ; intégration locale non destructive par merge. CI distante de cette PR : backend et PostgreSQL réussis (run 39250997406). Ces résultats ne valent pas validation du nouveau candidat.
- PR #7 : head `3f56ee00deb2e0ecb0bede1351edb0855e85e9c6`, base commune `42426b7b9539af25f07bfcb2e970837876027b63`. 195 fichiers, ~41 000 insertions : nouvelles migrations, identité self-service, facturation, rapports, workers planifiés. Recouvrement avec #9 dans auth/config/frontend/CI. Backend/frontend/restauration verts sur son ancien head, garde de revue sécurité et Vercel en échec. Non intégrée en bloc : nécessite réconciliation des migrations, du vérificateur RLS et revue humaine. Ses fonctionnalités ne sont pas déclarées livrées ici.
- Ordre : intégrer #9, reproduire/corriger les défauts indépendants, rejouer suites locales, publier candidat pour CI. Aucune fusion vers main.

## Défauts / dépendances / tests de clôture

| ID | Gravité | Preuve / cause racine | Responsable et correction | Dépendances / clôture |
|---|---|---|---|---|
| A-01 | Critique | main monte dev-login sans opt-in ; création de session pilote | A : protections opt-in/fail-closed de #9 intégrées | tests identité + refus staging/production |
| A-02 | Haute | vérificateur RLS initial fondé sur présence de politiques | A : vérification prédicats et tables de #9 intégrée | PostgreSQL réel, migrations vierges et tests d'injection de dérive |
| B-01 | Haute | HEAD et GET quarantaine non liés : remplacement entre métadonnées et lecture ; copie conditionnelle seule insuffisante contre aller-retour A/B/A | B : reproduire puis lier GET au même ETag que la copie | test remplacement avant lecture + test paramètre S3 IfMatch ; suite documents |
| T-01 | Haute | npm ci annonce 10 vulnérabilités (2 modérées/8 hautes) | Responsable : mises à jour compatibles, sans --force ; examiner reliquats | npm audit, typecheck, build |
| T-02 | Haute | workflow CI main ne contrôle pas le frontend | Responsable : ajouter installation verrouillée, typecheck et build | CI candidat |
| T-03 | Bloquant livraison | déploiement GitHub staging 7167225128 = workflow migration sans URL applicative ; Preview Vercel distinct | Responsable : instance locale isolée si possible ; pas de validation distante inventée | fournisseur OIDC, S3 privé, ClamAV, secrets staging non disponibles dans cet environnement ; hôtes externes non autorisés |

## Premiers contrôles observés

Après intégration #9 : `APP_ENV=test ../.venv/bin/python -m pytest -q` : 96 réussis, 11 PostgreSQL ignorés faute de DSN ; `npm ci`, `npm run typecheck`, `npm run build` réussis. `npm audit fix` applique des mises à jour compatibles mais termine en code 1 avec 7 alertes restantes. Les résultats finaux seront ajoutés après corrections.

## Corrections exécutées (responsable, sans sous-agent)

- **B-01 reproduit** : le nouveau test `test_quarantine_replacement_between_head_and_read_is_refused` obtenait réellement **201 clean** au lieu de 503 en alternant deux objets avant lecture puis avant copie. Correction : `GetObject(IfMatch=ETag)` et `CopyObject(CopySourceIfMatch=ETag)` utilisent la même révision ; test de contrat S3 ajouté. La copie conditionnelle préexistante est conservée. Tests faux stockage/scanner explicitement distincts d'une preuve S3/ClamAV réels.
- **A-03 ajouté, gravité haute** : première exécution PostgreSQL Unix-socket : 11 erreurs de fixture, cause `ConfigParser` interprète `%2F` dans l'URL Alembic. Correction : échappement `%` → `%%` lors de `set_main_option`, sans modifier l'URL SQLAlchemy effective. Test offline avec mot de passe encodé + preuve online sur socket Unix.
- **T-01 corrigé** : `npm audit fix` ne suffisait pas (7 alertes restantes). Mise à jour compatible de Next et dépendances verrouillées puis migration explicite Tailwind 4 et son plugin PostCSS ; configuration de thème conservée via `@config`. `npm audit` complet : **0 vulnérabilité**. Pas de `--force` ni suppression d'un contrôle.
- **T-02 appliqué** : job CI frontend ajouté (Node 22, npm ci, audit, typecheck, build).

## Contrôles locaux avant publication du candidat

- PostgreSQL réel obtenu via le paquet d'outillage local `pgserver` (non ajouté aux dépendances produit), cluster jetable sous `.cache/pgdata`, processus géré par Arena. Aucun service partagé utilisé.
- `APP_ENV=test VERICLAIM_TEST_POSTGRES_URL='<socket local jetable>' ../.venv/bin/python -m pytest -q` depuis backend : **110 passed, aucun skip**, y compris migrations vierges et les 11 tests PostgreSQL/RLS. Avertissement Starlette/httpx de dépréciation non bloquant.
- `npm run typecheck && npm run build && npm audit` depuis frontend : **codes 0**, Next 16.4.2, build statique réussi, **0 vulnérabilité**. Avertissement Node de détection automatique ESM du fichier de thème, non bloquant.
- `git diff --check` : code 0.
- Commandes incidentes rectifiées : lancement npm à la racine (pas de package.json), tentative d'écriture d'un test avec préfixe backend depuis backend, premier chemin pgserver sans parent `.cache`, assertion de test sur `calls` au lieu de `scanned_payloads`. Aucun de ces essais n'est compté comme réussi.

## Infrastructure distante constatée

GitHub expose les environnements Preview, Production et staging. La dernière Preview de #9 a le statut success et l'URL `https://vericlaim-6a5zo16g6-baya-ibraimmayoanouedu-8164s-projects.vercel.app` ; **elle n'a pas été testée depuis cette session**. Les seuls hôtes réseau permis ici sont GitHub, npm et PyPI. Aucune variable de connexion PostgreSQL distant/OIDC/Vercel n'est fournie au processus (noms vérifiés, aucune valeur affichée). Les secrets GitHub ne sont pas extractibles : aucun essai d'extraction. Aucun déploiement production ni migration staging/production déclenché.

## Limites de livraison, non closes

- PR #7 non intégrée : ses fonctionnalités de self-service, facturation et rapports signés restent à réconcilier ; ce n'est pas un blocage externe mais un chantier de code/revue encore ouvert.
- OIDC réel, stockage privé S3/MinIO, signatures ClamAV, traitement complet avec ces services, browser E2E et tâches planifiées distantes non validés. Le code de main contient deux workers exécutables en processus mais pas l'ordonnanceur serverless de #7. Une Preview ne prouve pas leur exécution.
- Staging complet non validé : provisionner et autoriser une application staging avec OIDC, PostgreSQL runtime/migrator séparés, stockage privé chiffré et ClamAV, démarrer les workers, puis rejouer les parcours authentifiés et documentaires réels. Ne pas activer dev-login pour contourner ce prérequis.
- Revue humaine requise sur auth/RLS/migrations et migration Tailwind (contrôle visuel), puis décision d'intégration ; aucune fusion vers main effectuée.
