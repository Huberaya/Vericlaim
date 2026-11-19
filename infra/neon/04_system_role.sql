-- Provisionne le rôle système chargé des parcours d'identité sans organisation.
--
-- Pourquoi un rôle de plus. Une RLS « organization_id = contexte » interdit par
-- construction les opérations qui n'ont pas encore d'organisation : inscription,
-- vérification d'adresse, réinitialisation de mot de passe, webhook prestataire.
-- Jusqu'ici, ces opérations passaient par le rôle applicatif, et la politique
-- devait donc rester permissive sur « organization_id IS NULL » — ce qui laissait
-- une organisation lire les jetons orphelins des autres, et refusait même
-- l'écriture d'une ligne rattachée (l'inscription échouait).
--
-- La séparation est faite ici, à la source : un rôle distinct, qui contourne la
-- RLS parce que ses opérations sont transverses par nature, et dont les droits
-- sont accordés **par colonne** par la migration
-- `c5f1a8e37b910_tenant_and_system_access_separation`.
--
-- Ce que ce rôle ne peut pas faire, et qui compte :
--   * lire le corps, le sujet ou le destinataire d'un e-mail ;
--   * lire une colonne d'identité non listée ;
--   * écrire autre chose que `used_at` sur un jeton, ou l'état d'un e-mail ;
--   * lire une table applicative (documents, analyses, preuves, factures).
--
-- Exécution : connecté à la base cible, avec un identifiant autorisé à créer des
-- rôles (le propriétaire de la base chez Neon). Le mot de passe est fourni par la
-- plateforme de déploiement via la variable :system_password — jamais ici.
--
--   psql "$ADMIN_DSN" -v target_database=vericlaim -v system_password="$VERICLAIM_SYSTEM_PASSWORD" \
--        -f infra/neon/04_system_role.sql
--
-- Le fichier est idempotent : une seconde exécution ne fait que réaffirmer les
-- attributs. Il refuse en revanche de réutiliser une identité privilégiée.

BEGIN;

-- psql n'interpole pas les variables à l'intérieur d'un bloc dollar-quoted : le
-- contrôle de longueur passe donc par format(), qui injecte le mot de passe
-- comme un littéral échappé, puis exécute le bloc.
SELECT format(
  $q$DO $guard$ BEGIN IF length(%L) < 24 THEN
       RAISE EXCEPTION 'Refusing bootstrap: the system password must be at least 24 characters.';
     END IF; END $guard$;$q$,
  :'system_password'
)
\gexec

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = 'vericlaim_system'
      AND (rolsuper OR rolcreaterole OR rolcreatedb)
  ) THEN
    RAISE EXCEPTION 'Refusing bootstrap: vericlaim_system holds creation privileges.';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_auth_members AS membership
    JOIN pg_roles AS granted ON granted.oid = membership.roleid
    JOIN pg_roles AS member ON member.oid = membership.member
    WHERE granted.rolname = 'vericlaim_system' OR member.rolname = 'vericlaim_system'
  ) THEN
    RAISE EXCEPTION 'Refusing bootstrap: vericlaim_system has role memberships.';
  END IF;
END
$$;

-- Première création : l'identité n'a pas de LOGIN tant que le mot de passe n'est
-- pas posé, ce qui rend un oubli visible (connexion refusée) plutôt que silencieux.
SELECT format(
  'CREATE ROLE vericlaim_system NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vericlaim_system')
\gexec

SELECT format(
  'ALTER ROLE vericlaim_system LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS PASSWORD %L',
  :'system_password'
)
\gexec

-- Aucun accès au schéma n'est accordé ici : la migration c5f1a8e37b910 accorde
-- les privilèges par colonne, après coup, et révoque tout accès préalable.

GRANT CONNECT ON DATABASE :"target_database" TO vericlaim_system;

COMMIT;

\echo 'Rôle vericlaim_system provisionné. Appliquer la migration c5f1a8e37b910 pour accorder ses droits par colonne.'
