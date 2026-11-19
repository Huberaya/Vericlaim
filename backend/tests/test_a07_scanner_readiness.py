"""A-07 : la readiness dit si clamd **répond**, pas seulement s'il est configuré.

Le défaut constaté n'était pas l'absence de scan — le service refusait déjà
correctement un fichier non analysé (fail-closed) — mais l'absence de *sonde* :
``/readyz`` répondait ``malware_scanner: configured`` même quand le démon était
mort. Une passerelle pouvait donc se croire prête et n'échouer qu'au moment où
un utilisateur déposait un fichier.

Ces tests utilisent un vrai serveur TCP local qui parle le protocole clamd
(``PING`` → ``PONG``, ``INSTREAM`` → verdict). Ils ne prétendent pas tester un
ClamAV complet : le binaire n'est pas disponible dans cet environnement, et
``test_a07_eicar`` plus bas dit exactement ce qui est vérifié.
"""

from __future__ import annotations

import socket
import threading

import pytest
from app.documents.scanner import (
    ClamAvMalwareScanner,
    DisabledMalwareScanner,
    MalwareScannerUnavailableError,
)

#: Le fichier de test EICAR, tel que défini publiquement. Il n'est pas un virus :
#: c'est une chaîne inerte que tout antivirus doit reconnaître.
EICAR = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
).encode("ascii")


class _FakeClamd:
    """Serveur TCP minimal qui parle le protocole clamd.

    Deux commandes suffisent et sont traitées séparément :

    * ``PING``  → ``PONG\0`` puis fermeture ;
    * ``INSTREAM`` → consommation des fragments jusqu'au terminateur, verdict
      terminé par NUL, puis fermeture de la connexion — c'est exactement ce que
      le client exige pour accepter un verdict.
    """

    def __init__(self, *, ping: bytes, verdict: bytes | None = None) -> None:
        self._ping = ping
        self._verdict = verdict if verdict is not None else b"stream: OK\x00"
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(16)
        self.port = self._socket.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_FakeClamd":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        try:
            self._socket.close()
        except OSError:
            pass
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            self._handle(connection)

    def _handle(self, connection: socket.socket) -> None:
        """Traite une connexion sans supposer de frontière entre les trames.

        Les frontières TCP sont arbitraires : la première lecture peut contenir
        la commande *et* le premier préfixe de longueur. Un serveur de test qui
        l'ignore produit des verdicts illisibles — c'est exactement l'ambiguïté
        que le client refuse, à raison.
        """

        buffer = b""

        def need(count: int) -> bytes | None:
            nonlocal buffer
            while len(buffer) < count:
                try:
                    chunk = connection.recv(65536)
                except OSError:
                    return None
                if not chunk:
                    return None
                buffer += chunk
            head, buffer = buffer[:count], buffer[count:]
            return head

        try:
            # Deux formats distincts, comme dans le vrai protocole :
            # « nPING\n » est terminé par un saut de ligne, « zINSTREAM\0 »
            # par un octet NUL. Attendre « \n » pour les deux bloquait
            # indéfiniment sur INSTREAM.
            # Le client sérialise ses envois : commande, puis longueur, puis
            # données. On ne boucle donc que tant que le tampon est **vide**.
            # Boucler jusqu'à ce qu'une trame entière soit reconnue bouclait à
            # l'infini sur INSTREAM — « zINSTREAM\0 » fait 10 octets et le
            # préfixe de longueur qui suit était avalé par l'attente.
            while not buffer:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                buffer += chunk
            if buffer.startswith(b"nPING"):
                connection.sendall(self._ping)
                return
            # « zINSTREAM\0 » fait 10 octets — pas 9. Une longueur fixe fausse
            # décale toutes les trames suivantes : le double lit alors la
            # longueur dans les données et ferme la connexion au milieu de
            # l'envoi (BrokenPipe côté client, de façon intermittente selon la
            # découpe TCP). On consomme donc jusqu'au terminateur.
            command_end = buffer.index(b"\0") + 1
            buffer = buffer[command_end:]
            while True:
                raw_size = need(4)
                if raw_size is None:
                    return
                size = int.from_bytes(raw_size, "big")
                if size == 0:
                    break
                if need(size) is None:
                    return
            connection.sendall(self._verdict)
        except OSError:
            pass
        finally:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()


def _scanner(port: int, timeout: int = 5) -> ClamAvMalwareScanner:
    return ClamAvMalwareScanner(host="127.0.0.1", port=port, timeout_seconds=timeout)


def test_ping_pong_marks_the_scanner_reachable() -> None:
    """clamd vivant : la sonde ne retourne aucune raison de refus."""

    with _FakeClamd(ping=b"PONG\x00") as server:
        assert _scanner(server.port).healthcheck() is None


def test_unexpected_ping_reply_is_not_healthy() -> None:
    """Une réponse qui n'est pas PONG ne vaut pas un succès."""

    with _FakeClamd(ping=b"UNKNOWN COMMAND\x00") as server:
        assert _scanner(server.port).healthcheck() == "scanner_unexpected_reply"


def test_unreachable_daemon_is_reported_not_swallowed() -> None:
    """Port fermé : la sonde retourne une raison, elle ne lève pas.

    La sonde est appelée par ``/readyz`` : elle doit décrire l'état, pas faire
    échouer la route de santé avec une trace.
    """

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    assert _scanner(free_port, timeout=1).healthcheck() == "scanner_unreachable"


def test_disabled_scanner_is_never_healthy() -> None:
    """Un scanner absent ne doit pas se présenter comme disponible."""

    assert DisabledMalwareScanner().healthcheck() == "scanner_disabled"


def test_eicar_is_detected_by_protocol_and_clean_file_is_clean() -> None:
    """EICAR via le protocole clamd réel : « FOUND », et jamais le nom du virus.

    Limite explicite : le verdict est produit par un serveur de test qui parle
    le protocole clamd, **pas** par un moteur ClamAV. Ce qui est prouvé ici est
    le traitement du verdict par notre client (rejet, non-fuite du nom de
    signature), pas la capacité d'un antivirus réel à reconnaître EICAR.
    """

    with _FakeClamd(ping=b"PONG\x00", verdict=b"stream: Eicar-Test-Signature FOUND\x00") as server:
        result = _scanner(server.port).scan(EICAR)
        assert result.is_clean is False
        assert result.engine == "clamav"
        # Le nom de signature ne doit jamais atteindre l'appelant.
        assert "Eicar" not in str(result.__dict__)

    with _FakeClamd(ping=b"PONG\x00", verdict=b"stream: OK\x00") as server:
        result = _scanner(server.port).scan(b"%PDF-1.7 harmless")
        assert result.is_clean is True


def test_ambiguous_verdict_is_an_error_not_a_clean_file() -> None:
    """Une réponse inexploitable ne devient jamais « propre »."""

    with _FakeClamd(ping=b"PONG\x00", verdict=b"stream: INTERNAL ERROR\x00") as server:
        with pytest.raises(Exception) as excinfo:
            _scanner(server.port).scan(b"%PDF-1.7")
        assert "propre" not in str(excinfo.value).lower()


def _check(body: dict, name: str) -> dict:
    """Contrôle nommé de la vue de readiness (contrat A-10)."""
    return next(check for check in body["checks"] if check["name"] == name)


def test_readiness_reports_reachability_separately(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/readyz distingue « configuré » de « joignable », sans casser l'ancien contrat.

    Un opérateur qui ne lit que ``malware_scanner`` garde sa sémantique
    précédente ; celui qui veut savoir si le moteur répond lit
    ``malware_scanner_reachable``.
    """

    from dataclasses import replace

    import app.main as main_module
    from app.documents.scanner import DisabledMalwareScanner
    from app.main import app
    from fastapi.testclient import TestClient

    # La vue de readiness décide d'interroger le moteur à partir du **mode
    # configuré**, pas de l'objet présent dans l'état applicatif : c'est ce qui
    # empêche un scanner absent de passer pour « prêt ». Le test doit donc dire
    # que le mode est `clamav`, sinon le contrôle reste « désactivé par
    # configuration » et le moteur injecté n'est jamais joint.
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, document_scanner_mode="clamav"),
    )

    with TestClient(app) as client:
        with _FakeClamd(ping=b"PONG\x00") as server:
            app.state.document_scanner = _scanner(server.port)
            body = client.get("/readyz").json()
            scanner = _check(body, "malware_scanner")
            storage = _check(body, "document_storage")
            # Le statut nomme l'état du moteur ; ce qui autorise l'import est la
            # capacité, exposée séparément. On vérifie les deux.
            assert scanner["status"] in {"ok", "configured"}
            # ``detail`` porte la trace du PING (« ping: pong »), pas une raison de
            # refus : ce qui compte est que le statut ne soit pas un échec et que
            # la capacité d'import soit accordée.
            assert scanner["detail"] in {None, "ping: pong"}
            assert body["capabilities"]["malware_scanning"] is True
            # L'import reste refusé ici si le *stockage* n'est pas configuré :
            # les deux dépendances sont indépendantes, et ce test ne prétend pas
            # monter un stockage réel (voir les tests de cycle documentaire).
            assert body["capabilities"]["document_storage"] is (
                storage["status"] == "ok"
            )
            dead_port = server.port

        # Port fermé garanti : on ouvre puis referme un socket pour obtenir un
        # numéro libre, plutôt que de réutiliser un port dont l'état dépend du
        # moment où le serveur de test s'est arrêté (TIME_WAIT).
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        app.state.document_scanner = _scanner(dead_port, timeout=1)
        body = client.get("/readyz").json()
        scanner = _check(body, "malware_scanner")
        assert scanner["status"] == "failed"
        # ``detail`` préfixe la raison par l'opération tentée ; c'est la raison,
        # et non sa mise en forme, qui est vérifiée.
        assert "scanner_unreachable" in scanner["detail"]
        assert body["capabilities"]["malware_scanning"] is False, (
            "un moteur muet doit refuser l'import"
        )
        # La readiness du processus ne dépend pas du moteur antivirus : la base
        # va bien, donc /readyz reste 200 et le trafic n'est pas coupé.
        assert _check(body, "database")["status"] == "ok"

        app.state.document_scanner = DisabledMalwareScanner()
        body = client.get("/readyz").json()
        scanner = _check(body, "malware_scanner")
        assert scanner["status"] != "ok"
        assert body["capabilities"]["malware_scanning"] is False


def test_fail_closed_never_promotes_an_unscanned_file() -> None:
    """Le service refuse un import quand le scan ne peut pas avoir lieu.

    C'est la garantie directement opposée à « un fichier non analysé promu
    comme sain ». Elle est ancienne dans le code ; le test l'épingle ici pour
    qu'une évolution de la readiness ne la relâche pas.
    """

    from app.documents.service import _record_scan_failure  # noqa: F401 - import de contrôle

    scanner = DisabledMalwareScanner()
    with pytest.raises(MalwareScannerUnavailableError):
        scanner.scan(b"%PDF-1.7")


def test_recette_clamd_stub_answers_the_readiness_probe() -> None:
    """Le harnais de recette doit se comporter comme un démon clamd réel.

    Défaut réel trouvé par la CI de recette : le stub n'implémentait que
    ``zINSTREAM`` et attendait un NUL sur **toutes** les commandes. Le client
    sonde avec ``nPING\\n`` (terminateur nouvelle ligne, conforme au protocole
    clamd) ; le stub ne répondait jamais, la sonde bloquait puis concluait
    « unreachable », et le parcours échouait sur
    ``document_import: refused``.

    La sonde avait raison, le harnais avait tort. Ce test empêche de
    « corriger » le problème en relâchant la sonde : il vérifie que c'est bien
    le stub qui répond, dans les deux framings, et qu'un stub muet est toujours
    détecté.
    """

    import socket
    import sys
    from pathlib import Path

    recette = Path(__file__).resolve().parents[2] / "tools" / "recette"
    if str(recette) not in sys.path:
        sys.path.insert(0, str(recette))

    from app.documents.scanner import ClamAvMalwareScanner
    from clamd_stub import ClamdStub

    stub = ClamdStub().start()
    try:
        scanner = ClamAvMalwareScanner(host=stub.host, port=stub.port, timeout_seconds=5)
        assert scanner.healthcheck() is None, "le stub sain doit être joignable"

        # Les deux framings du protocole répondent, avec leur propre terminateur.
        for payload, expected in ((b"nPING\n", b"PONG\n"), (b"zPING\x00", b"PONG\x00")):
            with socket.create_connection((stub.host, stub.port), timeout=5) as connection:
                connection.sendall(payload)
                assert connection.recv(64) == expected
    finally:
        stub.stop()

    # Un démon muet reste détecté : la sonde n'a pas été relâchée.
    silent = ClamdStub(ping_unreachable=True).start()
    try:
        scanner = ClamAvMalwareScanner(host=silent.host, port=silent.port, timeout_seconds=5)
        assert scanner.healthcheck() is not None
    finally:
        silent.stop()
