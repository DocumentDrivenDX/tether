"""Native TLS PostgreSQL controls for the isolated authentication oracle."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
import selected_profiles_pg_oracle as oracle
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from psycopg import sql
from selected_profiles_pg_oracle import probe_database


@dataclass(frozen=True)
class _OracleServer:
    host: str
    port: int
    user: str
    password: str
    database: str
    ca_file: str
    data_dir: Path
    infra_user: str


def _fabricated(*parts: str) -> str:
    return "-".join(("fabricated", *parts))


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _postgres_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError("local_openssl_postgres_required")
    return executable


def _run_postgres_tool(name: str, arguments: list[str], *, timeout: float) -> None:
    async def run() -> None:
        executable = _postgres_tool(name)
        environment = None
        if sys.platform == "darwin":
            # The postmaster refuses to start under an unset or "C" macOS locale.
            environment = {
                "LC_ALL": "en_US.UTF-8",
                "PATH": os.pathsep.join((str(Path(executable).parent), "/usr/bin", "/bin")),
            }
        process = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
        )
        try:
            async with asyncio.timeout(timeout):
                returncode = await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("local_postgres_tool_timeout") from None
        if returncode != 0:
            raise RuntimeError("local_postgres_tool_failed")

    asyncio.run(run())


def _certificate_pair(directory: Path, *, hostname: str) -> tuple[Path, Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Tether oracle test CA")])
    now = datetime.now(UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    server = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_file, cert_file, key_file = (
        directory / name for name in ("ca.crt", "server.crt", "server.key")
    )
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    return ca_file, cert_file, key_file


@pytest.fixture(scope="module")
def tls_postgres(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_OracleServer]:
    root = tmp_path_factory.mktemp("oracle-pg")
    data_dir = root / "data"
    data_dir.mkdir()
    ca_file, cert_file, key_file = _certificate_pair(root / "certs", hostname="localhost")
    _run_postgres_tool(
        "initdb",
        [
            "-D",
            str(data_dir),
            "--auth-local=trust",
            "--auth-host=scram-sha-256",
            "--encoding=UTF8",
            "-U",
            "postgres",
        ],
        timeout=30,
    )
    port = _port()
    socket_dir = Path(os.sep) / "tmp"
    config = data_dir / "postgresql.conf"
    with config.open("a", encoding="utf-8") as output:
        output.write(
            "\nlisten_addresses = '127.0.0.1'"
            f"\nport = {port}"
            "\nssl = on"
            f"\nssl_cert_file = '{cert_file}'"
            f"\nssl_key_file = '{key_file}'"
            "\npassword_encryption = 'scram-sha-256'\n"
        )
    _run_postgres_tool(
        "pg_ctl",
        [
            "-D",
            str(data_dir),
            "-w",
            "-o",
            f"-h 127.0.0.1 -p {port} -k {socket_dir}",
            "-l",
            str(data_dir / "log"),
            "start",
        ],
        timeout=30,
    )
    try:
        with psycopg.connect(
            host=str(socket_dir),
            port=port,
            dbname="postgres",
            user="postgres",
            sslmode="disable",
        ) as connection:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(_fabricated("caller")),
                    sql.Literal(_fabricated("fresh", "database", "credential")),
                )
            )
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(_fabricated("infra")),
                    sql.Literal(_fabricated("infra", "secret")),
                )
            )
        yield _OracleServer(
            host="localhost",
            port=port,
            user=_fabricated("caller"),
            password=_fabricated("fresh", "database", "credential"),
            database="postgres",
            ca_file=str(ca_file),
            data_dir=data_dir,
            infra_user=_fabricated("infra"),
        )
    finally:
        _run_postgres_tool("pg_ctl", ["-D", str(data_dir), "-m", "fast", "-w", "stop"], timeout=20)


def test_native_tls_oracle_distinguishes_auth_socket_and_tls_failures(tls_postgres) -> None:
    async def verify() -> None:
        accepted = await probe_database(
            host=tls_postgres.host,
            port=tls_postgres.port,
            user=tls_postgres.user,
            password=tls_postgres.password,
            database=tls_postgres.database,
            ca_file=tls_postgres.ca_file,
        )
        assert accepted.kind == "connected"
        assert accepted.current_user == tls_postgres.user
        assert accepted.driver_version == "0.32.0"

        wrong_role = await probe_database(
            host=tls_postgres.host,
            port=tls_postgres.port,
            user=tls_postgres.infra_user,
            password=tls_postgres.password,
            database=tls_postgres.database,
            ca_file=tls_postgres.ca_file,
        )
        assert wrong_role.kind == "auth_refused"
        assert wrong_role.sqlstate == "28P01"
        assert wrong_role.driver_version == "0.32.0"

        bad_port = _port()
        socket_drop = await probe_database(
            host="127.0.0.1",
            port=bad_port,
            user=tls_postgres.user,
            password=tls_postgres.password,
            database=tls_postgres.database,
            ca_file=tls_postgres.ca_file,
        )
        assert socket_drop.kind == "other_failure"
        assert socket_drop.sqlstate is None

        directory = tls_postgres.data_dir.parent / "certs" / "untrusted"
        untrusted_ca, _cert, _key = _certificate_pair(directory / "untrusted", hostname="localhost")
        untrusted_ca_result = await probe_database(
            host=tls_postgres.host,
            port=tls_postgres.port,
            user=tls_postgres.user,
            password=tls_postgres.password,
            database=tls_postgres.database,
            ca_file=str(untrusted_ca),
        )
        assert untrusted_ca_result.kind == "other_failure"
        assert untrusted_ca_result.sqlstate is None

        hostname_mismatch = await probe_database(
            host="127.0.0.1",
            port=tls_postgres.port,
            user=tls_postgres.user,
            password=tls_postgres.password,
            database=tls_postgres.database,
            ca_file=tls_postgres.ca_file,
        )
        assert hostname_mismatch.kind == "other_failure"
        assert hostname_mismatch.sqlstate is None

    asyncio.run(verify())


def test_oracle_refuses_driver_version_drift_before_connecting(monkeypatch) -> None:
    monkeypatch.setattr(oracle.asyncpg, "__version__", "0.31.0")
    with pytest.raises(RuntimeError, match="database_oracle_driver_version_mismatch"):
        asyncio.run(
            probe_database(
                host="localhost",
                port=1,
                user=_fabricated("caller"),
                password=_fabricated("credential"),
                database="postgres",
                ca_file="/fabricated/ca.pem",
            )
        )


def test_oracle_decoder_rejects_unpinned_child_report() -> None:
    payload = (
        json.dumps(
            {
                "kind": "auth_refused",
                "current_user": None,
                "sqlstate": "28P01",
                "driver_version": "0.31.0",
            }
        ).encode()
        + b"\n"
    )
    with pytest.raises(RuntimeError, match="database_oracle_child_failed"):
        oracle._decode_output(payload, 0)


def test_cancelling_probe_kills_and_reaps_its_owned_child(monkeypatch) -> None:
    class Input:
        def write(self, _payload: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

    class Child:
        pid = 12345

        def __init__(self) -> None:
            self.stdin = Input()
            self.stdout = asyncio.StreamReader()
            self.wait_started = asyncio.Event()
            self.killed = asyncio.Event()
            self.returncode: int | None = None
            self.wait_calls = 0

        async def wait(self) -> int:
            self.wait_calls += 1
            self.wait_started.set()
            await self.killed.wait()
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.killed.set()
            self.stdout.feed_eof()

    async def verify() -> None:
        child = Child()

        async def spawn(*_args: object, **_kwargs: object) -> Child:
            return child

        monkeypatch.setattr(oracle.asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(
            probe_database(
                host="localhost",
                port=1,
                user=_fabricated("caller"),
                password=_fabricated("credential"),
                database="postgres",
                ca_file="/fabricated/ca.pem",
            )
        )
        await child.wait_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.returncode == 0
        assert child.wait_calls >= 2

    asyncio.run(verify())
