"""Private Compose operator tools. All database targets belong to this project."""

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

RAW = Path("/var/lib/trusty/raw")
BACKUPS = Path("/backups")
FILES = ("database.dump", "snapshots.tar.gz")
REVISION = "SELECT version_num FROM alembic_version ORDER BY version_num"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_manifest(folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    if (
        not re.fullmatch(r"[a-f0-9]{40}", manifest["code_commit"])
        or not manifest["alembic_revision"]
    ):
        raise ValueError("Incomplete backup metadata")
    for name in FILES:
        if digest(folder / name) != manifest["sha256"][name]:
            raise ValueError(f"SHA-256 mismatch: {name}")
    return manifest


def sql(database, statement):
    return subprocess.check_output(
        ["psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database, "-c", statement], text=True
    ).strip()


def check_sources(rows, directory):
    count = 0
    for row_id, ref, expected in rows:
        path = Path(ref)
        if not path.is_relative_to(RAW) or ".." in path.parts:
            raise ValueError(f"Source {row_id}: invalid snapshot path")
        file = directory / path.relative_to(RAW)
        if not file.resolve().is_relative_to(directory.resolve()) or not file.is_file():
            raise ValueError(f"Source {row_id}: missing or invalid snapshot")
        if digest(file) != expected:
            raise ValueError(f"Source {row_id}: missing or mismatched SHA-256")
        count += 1
    return count


def check_database_sources(database, directory):
    query = "COPY (SELECT id, content_ref, content_sha256 FROM source_documents) TO STDOUT CSV"
    # Spool references to disk rather than holding a company's entire archive in memory.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="") as rows:
        subprocess.run(
            ["psql", "-Xq", "-v", "ON_ERROR_STOP=1", "-d", database, "-c", query],
            stdout=rows,
            check=True,
        )
        rows.seek(0)
        return check_sources(csv.reader(rows), directory)


def backup():
    commit = os.environ["TRUSTY_CODE_COMMIT"]
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Rebuild with TRUSTY_CODE_COMMIT set to git rev-parse HEAD")
    revision = sql("ibi", REVISION)
    if not revision:
        raise ValueError("Missing Alembic revision")
    check_database_sources("ibi", RAW)
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    with tempfile.TemporaryDirectory(dir=BACKUPS, prefix=".partial-") as temp:
        folder = Path(temp)
        subprocess.run(["pg_dump", "-Fc", "-d", "ibi", "-f", folder / FILES[0]], check=True)
        with tarfile.open(folder / FILES[1], "w:gz", dereference=True) as archive:
            for path in RAW.rglob("*"):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    raise ValueError(f"Unsupported snapshot entry: {path}")
                if path.is_file():
                    archive.add(path, arcname=str(path.relative_to(RAW)), recursive=False)
        manifest = {
            "code_commit": commit,
            "alembic_revision": revision,
            "sha256": {name: digest(folder / name) for name in FILES},
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        verify_manifest(folder)
        folder.rename(BACKUPS / name)
    print(f"Verified backup set: {name}")


def unpack(archive_path, directory):
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not member.isfile():
                raise ValueError(f"Unsafe archive member: {member.name}")
            dest = directory / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("xb") as output, archive.extractfile(member) as source:
                shutil.copyfileobj(source, output)
            dest.chmod(0o444)


def publish_snapshots(staged, raw):
    # Append-only: matching history is reusable. Never replace a different byte stream.
    for source in staged.rglob("*"):
        if source.is_file():
            target = raw / source.relative_to(staged)
            if target.resolve() != target or target.is_symlink():
                raise ValueError(f"Invalid existing snapshot path: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except FileExistsError:
                if not target.is_file() or digest(target) != digest(source):
                    raise ValueError(f"Existing snapshot differs: {target}") from None


def guard_target(replace):
    occupied = sql(
        "ibi",
        """SELECT EXISTS (
        SELECT FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN
            ('public', 'information_schema')
        UNION ALL SELECT FROM pg_depend WHERE refclassid = 'pg_namespace'::regclass
            AND refobjid = 'public'::regnamespace
        UNION ALL SELECT FROM pg_largeobject_metadata
        UNION ALL SELECT FROM pg_extension WHERE extname <> 'plpgsql'
        UNION ALL SELECT FROM pg_publication
        UNION ALL SELECT FROM pg_event_trigger)""",
    )
    if occupied != "f" and not replace:
        raise ValueError("Target database is non-empty. Stop the app and use --replace explicitly.")


def restore(name, replace):
    if not re.fullmatch(r"[0-9]{8}T[0-9]{12}Z", name):
        raise ValueError("Expected a timestamped backup set name")
    folder = BACKUPS / name
    manifest = verify_manifest(folder)
    guard_target(replace)
    suffix = uuid4().hex
    stage, previous = f"trusty_stage_{suffix}", f"trusty_previous_{suffix}"
    subprocess.run(["createdb", "--template=template0", stage], check=True)
    switched = False
    try:
        with tempfile.TemporaryDirectory(dir=RAW, prefix=".restore-") as temp:
            staged = Path(temp)
            unpack(folder / FILES[1], staged)
            subprocess.run(
                [
                    "pg_restore",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-acl",
                    "-d",
                    stage,
                    folder / FILES[0],
                ],
                check=True,
            )
            if sql(stage, REVISION) != manifest["alembic_revision"]:
                raise ValueError("Restored Alembic revision differs from manifest")
            count = check_database_sources(stage, staged)
            publish_snapshots(staged, RAW)
            check_database_sources(stage, RAW)
            # PostgreSQL refuses the rename if another session is connected.
            # Both renames commit together; the previous database remains recoverable.
            sql(
                "postgres",
                f"BEGIN; ALTER DATABASE ibi RENAME TO {previous}; "
                f"ALTER DATABASE {stage} RENAME TO ibi; COMMIT",
            )
            switched = True
        print(f"Restored {name}, verified {count} sources. Previous database: {previous}")
    finally:
        if not switched:
            subprocess.run(["dropdb", "--if-exists", stage], check=True)


def main():
    os.umask(0o077)
    commands = argparse.ArgumentParser()
    actions = commands.add_subparsers(dest="action", required=True)
    actions.add_parser("backup")
    load = actions.add_parser("restore")
    load.add_argument("set_name")
    load.add_argument("--replace", action="store_true")
    args = commands.parse_args()
    if args.action == "backup":
        backup()
    else:
        restore(args.set_name, args.replace)


if __name__ == "__main__":
    main()
