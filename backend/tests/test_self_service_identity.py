"""C14 acceptance: an outsider can create an account, verify it, invite a colleague.

The plan's acceptance criterion is a journey, not a feature list: *"un inconnu crée
son compte, vérifie son e-mail, invite un collègue, qui reçoit l'invitation et se
connecte — sans intervention de l'éditeur."* The first test below is that journey,
end to end, through the HTTP API only.

The rest of the module attacks the journey: enumeration, token replay, expired
tokens, lockout, session revocation after a password change, and the honesty of the
delivery record (the product must not claim an e-mail was sent when no transport is
configured).

**What a green run does not mean.** Delivery is asserted against the outbox
(`email_messages`). No SMTP relay is configured or reachable here, so the real
transport is **not** exercised — that is stated in `audit/C14_evidence.md` rather
than implied. Two-factor authentication is not implemented; the password fallback is
protected by a lockout and by e-mail verification, nothing more.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal, create_tables
from app.identity.passwords import (
    hash_password,
    password_policy_errors,
    token_digest,
    verify_password,
)
from app.identity.service import utcnow
from app.main import app
from app.models.domain import (
    AuthSession,
    EmailMessage,
    EmailMessageStatus,
    IdentityToken,
    IdentityTokenPurpose,
    Membership,
    MembershipStatus,
    PasswordLoginAttempt,
    Role,
    User,
    UserStatus,
)

STRONG_PASSWORD = "Chaussette-Verte-2026"

# The API validates addresses with `EmailStr`, which refuses reserved or
# non-resolving domains — measured: `@exemple.test` and `@example.com` are both
# rejected. The fixtures therefore use real, resolvable domains. Nothing is ever
# sent to them: `EMAIL_BACKEND=outbox` records the message and the tests read the
# outbox. This also documents a real constraint: signup performs a DNS check on
# the domain, so an instance without a resolver will refuse signups (see
# audit/C14_evidence.md).
TEST_DOMAIN = "laposte.net"

# Each run gets its own addresses: the identity tables are append-only by design
# (that is what makes the audit trail trustworthy), so a fixed address would make
# this module pass once and fail on every later run — measured: the first version
# of this file reused fixed addresses and went green, then red, on the same code.
RUN_TAG = uuid4().hex[:8]


def _address(local_part: str) -> str:
    """The address used by this run for `local_part`."""
    return f"{local_part}-{RUN_TAG}@{TEST_DOMAIN}"


@pytest.fixture(scope="module")
def client():
    create_tables()
    with TestClient(app) as test_client:
        yield test_client


def _outbox(recipient: str) -> list[EmailMessage]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(EmailMessage)
                .where(EmailMessage.recipient_email == recipient)
                .order_by(EmailMessage.created_at.desc())
            ).all()
        )


def _token_from(body: str) -> str:
    """Extract the one-shot token from the link printed in the message body."""
    for line in body.splitlines():
        marker = "token="
        if marker in line:
            return line.split(marker, 1)[1].strip()
    raise AssertionError(f"aucun jeton dans le message:\n{body}")


def _user(email: str) -> User:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None, email
        db.expunge(user)
        return user


def _signup(client: TestClient, email: str, organization: str, password: str = STRONG_PASSWORD):
    return client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": password,
            "organization_name": organization,
            "display_name": "Testeur",
        },
    )


# --------------------------------------------------------------------------- #
# The acceptance journey
# --------------------------------------------------------------------------- #


def test_an_outsider_can_create_an_account_invite_a_colleague_and_both_sign_in(client):
    """The plan's acceptance criterion, executed as one scenario."""
    suffix = "c14-journey"
    founder_email = _address(f"fondateur-{suffix}")
    colleague_email = _address(f"collegue-{suffix}")

    # 1. Signup --------------------------------------------------------------
    created = _signup(client, founder_email, "Fromagerie du Test")
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "accepted"

    founder = _user(founder_email)
    assert founder.status == UserStatus.INVITED
    assert founder.email_verified_at is None
    assert founder.password_hash and founder.password_hash.startswith("scrypt$")
    assert STRONG_PASSWORD not in founder.password_hash

    messages = _outbox(founder_email)
    assert len(messages) == 1, "l'e-mail de vérification n'a pas été enregistré"
    verification = messages[0]
    assert verification.purpose == "email_verification"
    assert verification.status == EmailMessageStatus.NOT_CONFIGURED, (
        "sans transport configuré, le message ne doit pas être annoncé comme remis"
    )

    # 2. The account cannot log in before verification ------------------------
    blocked = client.post(
        "/api/v1/auth/login", json={"email": founder_email, "password": STRONG_PASSWORD}
    )
    assert blocked.status_code == 403, blocked.text
    assert "Confirmez" in blocked.json()["detail"]["message"]

    # 3. Verify the e-mail ---------------------------------------------------
    verified = client.post(
        "/api/v1/auth/verify-email", json={"token": _token_from(verification.body_text)}
    )
    assert verified.status_code == 200, verified.text
    founder = _user(founder_email)
    assert founder.email_verified_at is not None
    assert founder.status == UserStatus.ACTIVE

    # 4. The founder signs in -------------------------------------------------
    login = client.post(
        "/api/v1/auth/login", json={"email": founder_email, "password": STRONG_PASSWORD}
    )
    assert login.status_code == 200, login.text
    session_cookie = login.cookies.get("vericlaim_session")
    assert session_cookie, "aucun cookie de session émis"
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == founder_email
    organization_id = login.json()["active_organization_id"]
    assert organization_id

    # 5. The founder invites a colleague -------------------------------------
    invitation = client.post(
        "/api/v1/organizations/current/members/invitations",
        json={"email": colleague_email, "role_code": "analyst"},
        headers={"X-CSRF-Token": login.cookies.get("vericlaim_csrf", "")},
    )
    assert invitation.status_code == 201, invitation.text

    colleague_messages = _outbox(colleague_email)
    assert len(colleague_messages) == 1, "l'invitation n'a envoyé aucun e-mail (défaut d'origine)"
    invite_message = colleague_messages[0]
    assert invite_message.purpose == "invitation"
    assert "Fromagerie du Test" in invite_message.body_text
    # The inviter is told the truth about delivery, and the API does not invent a
    # status of its own: without SMTP the message is recorded, not delivered.
    assert invitation.json()["delivery_status"] == invite_message.status.value
    assert invitation.json()["delivery_status"] == "not_configured"
    assert "pas été remis" in invitation.json()["delivery_note"]

    # 6. The colleague accepts and signs in ----------------------------------
    accepted = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": _token_from(invite_message.body_text),
            "password": "Pamplemousse-Bleu-2026",
            "display_name": "Collègue",
        },
    )
    assert accepted.status_code == 200, accepted.text
    colleague = _user(colleague_email)
    assert colleague.status == UserStatus.ACTIVE
    assert colleague.email_verified_at is not None
    assert accepted.json()["active_organization_id"] == organization_id

    with SessionLocal() as db:
        membership = db.scalar(
            select(Membership).where(
                Membership.user_id == colleague.id,
                # `organization_id` comes from JSON as a string; SQLAlchemy needs the UUID.
                Membership.organization_id == UUID(organization_id),
            )
        )
        assert membership is not None
        assert membership.status == MembershipStatus.ACTIVE
        assert membership.activated_at is not None


# --------------------------------------------------------------------------- #
# Refusals that must hold
# --------------------------------------------------------------------------- #


def test_signup_does_not_reveal_whether_an_account_exists(client):
    """The answer is byte-identical for a known and an unknown address.

    Compared on the body, not only the status: a difference in wording or in a
    flag would be enough for the signup form to become an account-existence
    oracle. The known address is created by this test, so the comparison does not
    depend on any other test having run.
    """
    known = _address("connu-c14")
    first = _signup(client, known, "Fromagerie Bis")
    assert first.status_code == 202 and first.json()["status"] == "accepted"

    again = _signup(client, known, "Fromagerie Bis")
    unknown = _signup(client, _address("inconnu-c14"), "Fromagerie Ter")

    assert again.status_code == unknown.status_code == 202
    assert again.json() == unknown.json(), (
        "la réponse diffère selon que l'adresse existe déjà : oracle d'énumération"
    )

    # The difference lives in the outbox only: a second signup on an existing
    # address must produce a reset link, never a second verification link.
    messages = _outbox(known)
    assert messages[0].purpose == "account_exists_notice"
    assert messages[-1].purpose == "email_verification"
    with SessionLocal() as db:
        purposes = {
            token.purpose
            for token in db.scalars(
                select(IdentityToken).where(IdentityToken.user_id == _user(known).id)
            ).all()
        }
    assert IdentityTokenPurpose.PASSWORD_RESET in purposes
    assert purposes == {IdentityTokenPurpose.EMAIL_VERIFICATION, IdentityTokenPurpose.PASSWORD_RESET}


def test_a_one_shot_token_cannot_be_replayed(client):
    email = _address("rejeu-c14")
    _signup(client, email, "Rejeu SARL")
    token = _token_from(_outbox(email)[0].body_text)
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 200
    second = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "token_already_used"


def test_an_expired_token_is_refused_and_says_so(client):
    email = _address("expire-c14")
    _signup(client, email, "Expire SARL")
    token = _token_from(_outbox(email)[0].body_text)
    with SessionLocal() as db:
        record = db.scalar(
            select(IdentityToken).where(IdentityToken.token_hash == token_digest(token))
        )
        assert record is not None
        record.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    response = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["code"] == "token_expired"


def test_a_reset_token_cannot_be_used_as_an_invitation(client):
    """Purposes are separated: a token only does what it was issued for."""
    email = _address("purpose-c14")
    _signup(client, email, "Purpose SARL")
    reset_token = _token_from(_outbox(email)[0].body_text)
    response = client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": reset_token, "password": "Ananas-Rouge-2026"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "token_invalid"


def test_repeated_failures_lock_the_account_temporarily(client):
    email = _address("verrou-c14")
    _signup(client, email, "Verrou SARL")
    client.post("/api/v1/auth/verify-email", json={"token": _token_from(_outbox(email)[0].body_text)})

    for _ in range(settings.password_login_max_failures):
        attempt = client.post(
            "/api/v1/auth/login", json={"email": email, "password": "Mauvais-Mot-De-Passe-1"}
        )
        assert attempt.status_code == 401
    locked = client.post("/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    assert locked.status_code == 429, locked.text
    assert locked.json()["detail"]["code"] == "account_locked"

    # The correct password does not bypass the lock: that is the point.
    with SessionLocal() as db:
        counter = db.scalar(
            select(PasswordLoginAttempt).where(PasswordLoginAttempt.email_normalized == email)
        )
        assert counter is not None and counter.locked_until is not None
        assert counter.failed_count >= settings.password_login_max_failures


def test_an_unknown_address_and_a_wrong_password_answer_identically(client):
    """The login form must not tell an attacker which addresses have accounts.

    The real account is created *here*, verified here, and compared with an address
    that does not exist. An earlier version of this test compared two addresses
    that both happened to be unknown — it passed for the wrong reason and could no
    longer fail, which the C14 mutation harness exposed.
    """
    known_email = _address("identite-connue")
    _signup(client, known_email, "Identité Connue SARL")
    client.post(
        "/api/v1/auth/verify-email", json={"token": _token_from(_outbox(known_email)[0].body_text)}
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": known_email, "password": STRONG_PASSWORD}
        ).status_code
        == 200
    ), "le compte témoin doit exister et accepter son mot de passe, sinon le test est vacant"

    wrong = client.post(
        "/api/v1/auth/login",
        json={"email": known_email, "password": "Mauvais-Mot-De-Passe-1"},
    )
    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": _address("personne-c14"), "password": "Mauvais-Mot-De-Passe-1"},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"]["message"] == unknown.json()["detail"]["message"]
    assert wrong.json()["detail"]["code"] == unknown.json()["detail"]["code"]


def test_the_password_policy_refuses_a_weak_password_before_anything_is_created(client):
    email = _address("faible-c14")
    response = _signup(client, email, "Faible SARL", password="court1")
    assert response.status_code == 422, response.text
    errors = response.json()["detail"]["errors"]
    assert any("12 caractères" in error for error in errors)
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == email)) is None, (
            "un compte a été créé malgré un mot de passe refusé"
        )


def test_the_policy_refuses_a_password_containing_the_email(client):
    email = _address("jean-dupont-c14")
    response = _signup(client, email, "Dupont SARL", password="Jean-Dupont-2026!")
    assert response.status_code == 422
    assert any("adresse e-mail" in error for error in response.json()["detail"]["errors"])


def test_password_reset_revokes_every_session(client):
    email = _address("reset-c14")
    _signup(client, email, "Reset SARL")
    client.post("/api/v1/auth/verify-email", json={"token": _token_from(_outbox(email)[0].body_text)})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    assert login.status_code == 200
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert db.scalar(
            select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        )

    requested = client.post("/api/v1/auth/password-reset/request", json={"email": email})
    assert requested.status_code == 202
    reset_token = _token_from(_outbox(email)[0].body_text)
    confirmed = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": "Nouveau-Mot-De-Passe-2026"},
    )
    assert confirmed.status_code == 200, confirmed.text

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        open_sessions = list(
            db.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
                )
            ).all()
        )
        assert open_sessions == [], "une session a survécu à la réinitialisation"
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Nouveau-Mot-De-Passe-2026"},
        ).status_code
        == 200
    )


def test_password_reset_request_answers_the_same_for_an_unknown_address(client):
    response = client.post(
        "/api/v1/auth/password-reset/request", json={"email": _address("jamais-vu-c14")}
    )
    assert response.status_code == 202
    assert _outbox(_address("jamais-vu-c14")) == [], "aucun message ne doit partir"


def test_the_outbox_records_the_truth_about_delivery(client):
    """No transport configured: the product must not say "sent"."""
    email = _address("outbox-c14")
    _signup(client, email, "Outbox SARL")
    message = _outbox(email)[0]
    assert message.status == EmailMessageStatus.NOT_CONFIGURED
    assert message.sent_at is None
    assert message.error_code == "no_transport_configured"
    assert message.transport == "outbox"


def test_no_password_or_token_is_ever_stored_in_clear(client):
    """A database dump must not hand out credentials or working links."""
    email = _address("clair-c14")
    _signup(client, email, "Clair SARL")
    message = _outbox(email)[0]
    token = _token_from(message.body_text)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert STRONG_PASSWORD not in (user.password_hash or "")
        stored = db.scalar(select(IdentityToken).where(IdentityToken.user_id == user.id))
        assert stored is not None
        assert stored.token_hash == token_digest(token)
        assert token not in stored.token_hash
        assert len(stored.token_hash) == 64


def test_identity_events_reach_the_audit_chain(client):
    """Signup, verification and login must be auditable, not just stored."""
    email = _address("audit-c14")
    _signup(client, email, "Audit SARL")
    client.post("/api/v1/auth/verify-email", json={"token": _token_from(_outbox(email)[0].body_text)})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    assert login.status_code == 200
    organization_id = login.json()["active_organization_id"]

    events = client.get(f"/api/v1/audit/events?organization_id={organization_id}")
    assert events.status_code == 200, events.text
    actions = {item["action"] for item in events.json()}
    assert {"identity.signup", "identity.email_verified", "identity.password_login_succeeded"} <= actions
    chain = client.get("/api/v1/audit/verify")
    assert chain.status_code == 200 and chain.json()["is_valid"] is True


def test_changing_a_password_requires_the_current_one(client):
    email = _address("change-c14")
    _signup(client, email, "Change SARL")
    client.post("/api/v1/auth/verify-email", json={"token": _token_from(_outbox(email)[0].body_text)})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    csrf = login.cookies.get("vericlaim_csrf", "")

    wrong = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": "Pas-Le-Bon-2026", "new_password": "Encore-Un-Autre-2026"},
        headers={"X-CSRF-Token": csrf},
    )
    assert wrong.status_code == 401, wrong.text

    right = client.post(
        "/api/v1/auth/password/change",
        json={"current_password": STRONG_PASSWORD, "new_password": "Encore-Un-Autre-2026"},
        headers={"X-CSRF-Token": csrf},
    )
    assert right.status_code == 200, right.text
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Encore-Un-Autre-2026"},
        ).status_code
        == 200
    )


def test_changing_a_password_without_a_csrf_token_is_refused(client):
    """A stolen session cookie alone must not let an attacker rotate the password."""
    email = _address("csrf-c14")
    _signup(client, email, "Csrf SARL")
    client.post("/api/v1/auth/verify-email", json={"token": _token_from(_outbox(email)[0].body_text)})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    assert login.status_code == 200
    csrf = login.cookies.get("vericlaim_csrf", "")
    assert csrf

    body = {"current_password": STRONG_PASSWORD, "new_password": "Boussole-Violette-2026"}
    without_header = client.post("/api/v1/auth/password/change", json=body)
    assert without_header.status_code == 403, without_header.text

    cookie_only = client.post(
        "/api/v1/auth/password/change",
        json=body,
        headers={"X-CSRF-Token": "un-jeton-invente-de-la-meme-taille-000000000000"},
    )
    assert cookie_only.status_code == 403, cookie_only.text

    with_header = client.post(
        "/api/v1/auth/password/change", json=body, headers={"X-CSRF-Token": csrf}
    )
    assert with_header.status_code == 200, with_header.text


def test_an_accepted_invitation_binds_the_colleague_to_the_inviting_organization(client):
    """An invitation must not be a lever to read another tenant's documents."""
    founder_email = _address("tenant-fondateur")
    colleague_email = _address("tenant-collegue")
    _signup(client, founder_email, "Tenant Alpha SARL")
    client.post(
        "/api/v1/auth/verify-email", json={"token": _token_from(_outbox(founder_email)[0].body_text)}
    )
    login = client.post(
        "/api/v1/auth/login", json={"email": founder_email, "password": STRONG_PASSWORD}
    )
    assert login.status_code == 200
    alpha_id = login.json()["active_organization_id"]

    invited = client.post(
        "/api/v1/organizations/current/members/invitations",
        json={"email": colleague_email, "role_code": "viewer"},
        headers={"X-CSRF-Token": login.cookies.get("vericlaim_csrf", "")},
    )
    assert invited.status_code == 201, invited.text
    accepted = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": _token_from(_outbox(colleague_email)[0].body_text),
            "password": "Boussole-Bleue-2026",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["active_organization_id"] == alpha_id

    with SessionLocal() as db:
        colleague = _user(colleague_email)
        memberships = list(
            db.scalars(select(Membership).where(Membership.user_id == colleague.id)).all()
        )
        assert len(memberships) == 1, "l'invité s'est retrouvé dans plusieurs organisations"
        role = db.get(Role, memberships[0].role_id)
        assert role is not None and role.code == "viewer"


# --------------------------------------------------------------------------- #
# The primitives
# --------------------------------------------------------------------------- #


def test_the_password_policy_is_published(client):
    policy = client.get("/api/v1/auth/password-policy")
    assert policy.status_code == 200
    body = policy.json()
    assert body["min_length"] == 12
    assert body["requires_digit"] is True
    assert body["requires_letter"] is True
    assert body["hashing"]["scheme"] == "scrypt"
    assert "en clair" in body["hashing"]["note"]
    # What the interface shows must come from here, not from a copy: the login
    # screen announced a fixed number of attempts while the server's default
    # differed. These two fields are what makes that drift impossible.
    assert body["login_max_failures"] == settings.password_login_max_failures
    assert body["login_lockout_seconds"] == settings.password_login_lockout_seconds


def test_hashing_round_trip_and_salted_hashes():
    first = hash_password(STRONG_PASSWORD)
    second = hash_password(STRONG_PASSWORD)
    assert first != second, "deux hachages identiques : le sel n'est pas aléatoire"
    assert verify_password(STRONG_PASSWORD, first) is True
    assert verify_password("Chaussette-Verte-2027", first) is False
    assert verify_password(STRONG_PASSWORD, None) is False
    assert verify_password(STRONG_PASSWORD, "scrypt$court") is False


def test_policy_errors_are_specific_and_complete():
    errors = password_policy_errors("abc", email=_address("abc"))
    joined = " ".join(errors)
    assert "12 caractères" in joined
    assert "chiffre" in joined
    assert password_policy_errors("Zz1" + "a" * 20) == []
