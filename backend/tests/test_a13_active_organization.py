"""A-13 (second volet) : une lecture ne doit pas détruire une organisation active.

Le défaut observé en CI — ``active_organization=None`` au milieu d'un parcours,
puis 409 sur les appels suivants — venait de ``resolve_active_membership`` : une
lecture qui ne ramenait aucune ligne écrivait ``active_organization_id = None``
puis ``flush``. Une lecture infructueuse devenait donc une décision persistante,
et l'utilisateur perdait son espace sans rien avoir demandé.

La correction distingue les causes au lieu de les confondre :

* membership absent, membership inactif, organisation absente ou désactivée :
  l'état est réellement caduc, on l'efface **et on le trace** ;
* lecture qui échoue (erreur de base, incident transitoire) : l'exception
  remonte, rien n'est écrit — un incident passager ne doit pas coûter une
  sélection ;
* membership actif mais vue incohérente (organisation invisible, rôle manquant) :
  on lève une erreur explicite et on conserve la sélection, car effacer
  reviendrait à traiter un défaut de lecture comme une décision métier.

Les tests ci-dessous échouent sans la correction : ils exigent que la sélection
survive à une lecture en erreur et à une vue incohérente.
"""

from __future__ import annotations

import app.identity.service as identity_service
import pytest
from app.core.database import SessionLocal
from app.identity.service import CurrentSession, resolve_active_membership
from app.models.domain import (
    AuthSession,
    Membership,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from tests.auth_support import authenticate_client


def _active_session(db, identity) -> CurrentSession:
    """Recharge la session réelle créée pour le client de test."""

    from app.core.config import settings
    from app.identity.security import token_hmac

    token_hash = token_hmac(identity.session_token, settings.auth_session_secret)
    auth_session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))
    assert auth_session is not None
    user = db.get(User, identity.user_id)
    assert user is not None
    return CurrentSession(session=auth_session, user=user)


def test_transient_read_error_does_not_erase_the_active_organization() -> None:
    """Une lecture qui échoue laisse la sélection intacte et remonte l'erreur."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        with SessionLocal() as db:
            current = _active_session(db, identity)
            before = current.session.active_organization_id
            assert before == identity.organization_id

            original_execute = db.execute

            def failing_execute(*args, **kwargs):
                raise OperationalError("SELECT 1", {}, Exception("connexion perdue"))

            db.execute = failing_execute  # type: ignore[method-assign]
            with pytest.raises(OperationalError):
                resolve_active_membership(db, current)
            db.execute = original_execute  # type: ignore[method-assign]

            db.refresh(current.session)
            assert current.session.active_organization_id == before, (
                "une erreur de lecture transitoire a effacé l'organisation active"
            )


def test_incoherent_membership_view_keeps_the_selection() -> None:
    """Membership actif + organisation active : on n'efface pas, on signale.

    C'est le cas qui produisait la perte silencieuse : la jointure ne ramenait
    rien alors que le membership était bien actif. Effacer la sélection faisait
    passer un défaut de lecture pour une décision de l'utilisateur.
    """

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        with SessionLocal() as db:
            current = _active_session(db, identity)
            before = current.session.active_organization_id

            # Jointure rendue vide sans toucher aux données réelles : on simule
            # une lecture incomplète (rôle manquant, vue filtrée).
            original_execute = db.execute
            calls = {"n": 0}

            def execute_with_one_empty_join(statement, *args, **kwargs):
                calls["n"] += 1
                result = original_execute(statement, *args, **kwargs)
                if calls["n"] == 1:
                    return _EmptyResult()
                return result

            db.execute = execute_with_one_empty_join  # type: ignore[method-assign]
            # Référence résolue à l'exécution : sans la correction, la classe
            # n'existe pas et c'est bien le comportement qui doit faire échouer
            # le test, pas une erreur d'import à la collecte.
            incoherent = getattr(identity_service, "IdentityNotResolvableError", AssertionError)
            try:
                with pytest.raises(incoherent):
                    resolve_active_membership(db, current)
            finally:
                db.execute = original_execute  # type: ignore[method-assign]

            db.refresh(current.session)
            assert current.session.active_organization_id == before, (
                "une vue incohérente a effacé l'organisation active"
            )


class _EmptyResult:
    """Résultat de jointure vide, sans ligne."""

    def one_or_none(self):
        return None


def test_genuinely_removed_membership_is_cleared_and_traced(caplog) -> None:
    """Un membership réellement retiré efface la sélection, et le dit."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        with SessionLocal() as db:
            membership = db.scalar(
                select(Membership).where(
                    Membership.user_id == identity.user_id,
                    Membership.organization_id == identity.organization_id,
                )
            )
            assert membership is not None
            membership.status = MembershipStatus.REVOKED
            db.flush()

            current = _active_session(db, identity)
            with caplog.at_level("WARNING"):
                view = resolve_active_membership(db, current)

            assert view is None
            assert current.session.active_organization_id is None
            rendered = [record.getMessage() for record in caplog.records]
            assert any("membership_inactive" in line for line in rendered), (
                f"l'effacement d'une sélection doit être tracé avec sa cause: {rendered}"
            )


def test_inactive_organization_is_cleared() -> None:
    """Une organisation désactivée est une cause durable : la sélection tombe."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        with SessionLocal() as db:
            organization = db.get(Organization, identity.organization_id)
            assert organization is not None
            organization.status = OrganizationStatus.SUSPENDED
            db.flush()

            current = _active_session(db, identity)
            view = resolve_active_membership(db, current)
            assert view is None
            assert current.session.active_organization_id is None


def test_missing_membership_row_is_cleared() -> None:
    """Membership supprimé : la sélection ne peut plus désigner un espace vivant."""

    from app.main import app

    with TestClient(app) as client:
        identity = authenticate_client(client, role_code="analyst")
        with SessionLocal() as db:
            membership = db.scalar(
                select(Membership).where(
                    Membership.user_id == identity.user_id,
                    Membership.organization_id == identity.organization_id,
                )
            )
            assert membership is not None
            db.delete(membership)
            db.flush()

            current = _active_session(db, identity)
            view = resolve_active_membership(db, current)
            assert view is None
            assert current.session.active_organization_id is None
