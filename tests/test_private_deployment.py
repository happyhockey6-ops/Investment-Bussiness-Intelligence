"""Deployment contracts run without Docker, sockets or external data."""

import hashlib
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from deploy import recovery
from pydantic import ValidationError
from sqlalchemy import create_engine, event

from ibi.api import __main__ as entry
from ibi.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_compose_privacy_and_persistence():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    for name, service in services.items():
        assert "network_mode" not in service
        if name != "app":
            assert not service.get("ports")
        if name not in {"app", "db"}:
            assert service["profiles"] == ["tools"]
            assert service["entrypoint"][:3] == ["flock", "--exclusive", "/var/lock/trusty"]
            assert "maintenance:/var/lock/trusty:ro" in service["volumes"]
        snapshots = [
            v for v in service["volumes"] if isinstance(v, str) and v.startswith("snapshots:")
        ]
        if name in {"app", "backup"}:
            assert snapshots == ["snapshots:/var/lib/trusty/raw:ro"]
        elif name in {"ingest", "listings", "restore"}:
            assert snapshots == ["snapshots:/var/lib/trusty/raw"]
        else:
            assert not snapshots
    app = services["app"]
    assert app["ports"] == ["127.0.0.1:${IBI_API_PORT:-8000}:8000"]
    assert app["depends_on"]["db"]["condition"] == "service_healthy"
    assert app["restart"] == "unless-stopped"
    assert "command" not in app and "entrypoint" not in app
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert services["db"]["image"].startswith("postgres:16.")
    assert "@sha256:" in services["db"]["image"]
    assert services["db"]["volumes"] == ["pgdata:/var/lib/postgresql/data"]
    assert services["restore"]["volumes"][-1]["read_only"] is True


def test_image_runtime_contract():
    lines = (ROOT / "Dockerfile").read_text().splitlines()
    bases = [line.split()[1] for line in lines if line.startswith("FROM ")]
    assert len(bases) == 2 and all("@sha256:" in image for image in bases)
    assert (
        yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["db"]["image"]
        in bases
    )
    assert [line for line in lines if line.startswith("USER ")] == [
        "USER 10001:10001",
        "USER trusty",
    ]
    assert [line for line in lines if line.startswith("CMD ")] == [
        'CMD ["python", "-m", "ibi.api"]'
    ]
    assert any(line.startswith("HEALTHCHECK ") and "/healthz" in line for line in lines)


def test_environment_example_and_default_host(monkeypatch):
    for name in ("IBI_API_HOST", "IBI_DEPLOYMENT", "IBI_SEC_USER_AGENT"):
        monkeypatch.delenv(name, raising=False)
    assert Settings(_env_file=None).api_host == "127.0.0.1"
    example = Settings(_env_file=ROOT / "deploy/.env.example")
    with pytest.raises(RuntimeError, match="monitored contact"):
        example.require_sec_user_agent()


@pytest.mark.parametrize(
    ("host", "mode", "allowed"),
    [
        ("::1", "local", True),
        ("127.0.0.2", "local", True),
        ("0.0.0.0", "local", False),
        ("0.0.0.0", "container", True),
        ("::", "container", False),
        ("192.168.0.3", "container", False),
        ("example.com", "local", False),
    ],
)
def test_bind_policy(monkeypatch, host, mode, allowed):
    monkeypatch.setenv("IBI_API_HOST", host)
    monkeypatch.setenv("IBI_DEPLOYMENT", mode)
    if allowed:
        assert Settings(_env_file=None).api_host == host
    else:
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


@pytest.mark.parametrize("state", ["empty", "behind", "head"])
def test_startup_only_reads_schema(monkeypatch, state):
    db = create_engine("sqlite://")
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    if state != "empty":
        with db.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE alembic_version (version_num TEXT)")
            connection.exec_driver_sql(
                "INSERT INTO alembic_version VALUES (?)", (head if state == "head" else "old",)
            )
    queries = []
    event.listen(db, "before_cursor_execute", lambda c, cur, sql, *rest: queries.append(sql))
    monkeypatch.setattr(entry, "build_engine", lambda settings: db)
    monkeypatch.setattr(entry, "get_settings", lambda: Settings(api_host="::1", api_port=8765))
    monkeypatch.setattr(command, "upgrade", Mock(side_effect=AssertionError("automatic migration")))
    serve = Mock()
    monkeypatch.setattr(entry.uvicorn, "run", serve)
    if state == "head":
        entry.main()
        assert serve.call_args.kwargs == {"host": "::1", "port": 8765}
    else:
        with pytest.raises(RuntimeError, match="docker compose run --rm migrate"):
            entry.main()
        serve.assert_not_called()
    assert queries and all(q.lstrip().split()[0].upper() in {"PRAGMA", "SELECT"} for q in queries)
    command.upgrade.assert_not_called()


@pytest.mark.parametrize("damaged", [None, *recovery.FILES])
def test_backup_manifest_and_restore_preflight(tmp_path, monkeypatch, damaged):
    root, raw = tmp_path / "backups", tmp_path / "raw"
    root.mkdir()
    raw.mkdir()
    (raw / "example").write_bytes(b"original")
    monkeypatch.setattr(recovery, "BACKUPS", root)
    monkeypatch.setattr(recovery, "RAW", raw)
    monkeypatch.setenv("TRUSTY_CODE_COMMIT", "a" * 40)
    monkeypatch.setattr(recovery, "sql", lambda db, sql: "test-revision")
    monkeypatch.setattr(recovery, "check_database_sources", lambda db, path: 1)

    def dump(args, check):
        assert args[:4] == ["pg_dump", "-Fc", "-d", "ibi"]
        args[-1].write_bytes(b"fixture dump")

    monkeypatch.setattr(recovery.subprocess, "run", dump)
    recovery.backup()
    (folder,) = root.iterdir()
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["code_commit"] == "a" * 40 and manifest["alembic_revision"] == "test-revision"
    assert manifest["sha256"] == {
        name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in recovery.FILES
    }
    if damaged:
        (folder / damaged).write_bytes(b"changed")
        guard = Mock(side_effect=AssertionError("target contacted before verification"))
        monkeypatch.setattr(recovery, "guard_target", guard)
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            recovery.restore(folder.name, False)
        guard.assert_not_called()
    else:
        assert recovery.verify_manifest(folder) == manifest
        with pytest.raises(ValueError, match="non-empty"):
            recovery.restore(folder.name, False)
        recovery.guard_target(True)


def test_provenance_and_append_only_publish(tmp_path):
    stage, raw = tmp_path / "stage", tmp_path / "raw"
    stage.mkdir()
    raw.mkdir()
    rows = [(1, str(recovery.RAW / "a"), hashlib.sha256(b"exact").hexdigest())]
    with pytest.raises(ValueError, match="Source 1: missing"):
        recovery.check_sources(rows, stage)
    (stage / "a").write_bytes(b"altered")
    with pytest.raises(ValueError, match="SHA-256"):
        recovery.check_sources(rows, stage)
    (stage / "a").write_bytes(b"exact")
    assert recovery.check_sources(rows, stage) == 1
    with pytest.raises(ValueError, match="SHA-256"):
        recovery.check_sources([(1, rows[0][1], "")], stage)
    (raw / "a").write_bytes(b"prior bytes")
    with pytest.raises(ValueError, match="Existing snapshot differs"):
        recovery.publish_snapshots(stage, raw)
    assert (raw / "a").read_bytes() == b"prior bytes"
    (raw / "a").unlink()
    recovery.publish_snapshots(stage, raw)
    recovery.publish_snapshots(stage, raw)
    assert recovery.check_sources(rows, raw) == 1


@pytest.mark.parametrize("name,kind", [("../outside", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)])
def test_restore_rejects_unsafe_archive_members(tmp_path, name, kind):
    archive = tmp_path / "archive.tar.gz"
    with tarfile.open(archive, "w:gz") as writer:
        member = tarfile.TarInfo(name)
        member.type = kind
        writer.addfile(member, io.BytesIO())
    with pytest.raises(ValueError, match="Unsafe archive"):
        recovery.unpack(archive, tmp_path)
