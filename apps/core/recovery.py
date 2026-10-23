"""Encrypted maintenance snapshots for one dedicated office.

Stop/drain the app and all writer commands first. The maintenance flag records
that prerequisite; it cannot stop a process configured with another environment.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection

from apps.audit.services import verify_chain
from apps.core.crypto import master_cipher
from apps.core.models import Tenant

MAGIC = b"LEXFLOW-BACKUP-1\n"


def _cipher():
    if not settings.LEXFLOW_MAINTENANCE:
        raise ValidationError("Stop/drain all writers and set LEXFLOW_MAINTENANCE=1 before backup or restore.")
    try:
        cipher = Fernet(settings.LEXFLOW_BACKUP_KEY.encode())
    except (ValueError, TypeError):
        raise ValidationError("Set a separate valid LEXFLOW_BACKUP_KEY. Escrow it separately from the backup.")
    if settings.LEXFLOW_MASTER_KEY == "fernet:" + settings.LEXFLOW_BACKUP_KEY:
        raise ValidationError("Use a backup key distinct from the application master key.")
    return cipher


def _pg_environment():
    db = settings.DATABASES["default"]
    return {**os.environ, "PGPASSWORD": db.get("PASSWORD", ""), "PGHOST": db.get("HOST", ""),
            "PGPORT": str(db.get("PORT") or "5432"), "PGUSER": db.get("USER", ""),
            "PGDATABASE": str(db["NAME"])}


def _pg(args):
    try:
        subprocess.run(args, env=_pg_environment(), check=True, capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationError("PostgreSQL backup/restore failed. Check server, client tools and operator access.") from exc


def _files(root, prefix):
    root = Path(root)
    if root.is_symlink():
        raise ValidationError("Snapshot roots must not be symlinks.")
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValidationError("Snapshot directories must not contain symlinks.")
            if path.is_file():
                yield prefix + "/" + path.relative_to(root).as_posix(), path


def create(path):
    cipher = _cipher()
    path = Path(path)
    if path.exists():
        raise ValidationError("Choose a new backup filename; existing backups are never overwritten.")
    offices = list(Tenant.objects.all())
    if len(offices) != 1:
        raise ValidationError("A dedicated snapshot requires exactly one office.")
    office = offices[0]
    if not verify_chain(office).ok:
        raise ValidationError("Resolve the audit integrity failure before taking a trusted snapshot.")
    from apps.intake.models import Attachment
    for attachment in Attachment.objects.exclude(blob_name=""):
        if not (attachment.tenant.vault_dir / attachment.blob_name).is_file():
            raise ValidationError("A referenced source blob is missing; investigate before backup.")
    with tempfile.TemporaryDirectory(prefix="lexflow-backup-") as temp:
        dump = Path(temp) / "database.dump"
        vendor = connection.vendor
        if vendor == "sqlite":
            connection.ensure_connection()
            with sqlite3.connect(dump) as target:
                connection.connection.backup(target)
        elif vendor == "postgresql":
            _pg(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(dump)])
        else:
            raise ValidationError("Unsupported database for recovery.")
        dump.chmod(0o600)
        entries = [("database.dump", dump), *_files(settings.LEXFLOW_VAULT_ROOT, "vault"),
                   *_files(settings.LEXFLOW_EXPORT_ROOT, "exports")]
        if sum(source.stat().st_size for _, source in entries) > settings.LEXFLOW_BACKUP_MAX_BYTES:
            raise ValidationError("Snapshot exceeds the pilot size limit; use an operator-managed backup service.")
        manifest = {"version": 1, "database": vendor, "created_at": datetime.now(timezone.utc).isoformat(),
                    "office": str(office.uid), "slug": office.slug, "key_fingerprint": office.key_fingerprint,
                    "audit": {"seq": office.audit_seq, "head": office.audit_head, "seal": office.audit_seal},
                    "master_check": master_cipher().encrypt(b"lexflow-recovery-master-v1").decode(), "files": {}}
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, source in entries:
                data = source.read_bytes()
                manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o600
                archive.addfile(info, io.BytesIO(data))
            data = json.dumps(manifest, sort_keys=True).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mode = len(data), 0o600
            archive.addfile(info, io.BytesIO(data))
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(MAGIC + cipher.encrypt(stream.getvalue()))
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return {key: manifest[key] for key in ("version", "database", "created_at", "office", "audit", "key_fingerprint")}


def unpack(path, target):
    cipher = _cipher()
    path = Path(path)
    if path.stat().st_size > settings.LEXFLOW_BACKUP_MAX_BYTES * 2:
        raise ValidationError("Backup exceeds the pilot recovery size limit.")
    encoded = path.read_bytes()
    try:
        if not encoded.startswith(MAGIC):
            raise ValueError
        data = cipher.decrypt(encoded[len(MAGIC):])
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            names, total = set(), 0
            for member in members:
                name = PurePosixPath(member.name)
                if (not member.isfile() or name.is_absolute() or ".." in name.parts or
                        member.name in names or name.as_posix() != member.name or
                        member.name not in ("manifest.json", "database.dump") and name.parts[0] not in ("vault", "exports")):
                    raise ValueError
                names.add(member.name)
                total += member.size
            if total > settings.LEXFLOW_BACKUP_MAX_BYTES + 1024 * 1024:
                raise ValueError
            manifest = json.loads(archive.extractfile("manifest.json").read())
            if (manifest["version"] != 1 or "database.dump" not in manifest["files"] or
                    names != set(manifest["files"]) | {"manifest.json"}):
                raise ValueError
            if master_cipher().decrypt(manifest["master_check"].encode()) != b"lexflow-recovery-master-v1":
                raise ValueError
            if settings.LEXFLOW_TENANT_SLUG and manifest["slug"] != settings.LEXFLOW_TENANT_SLUG:
                raise ValueError
            for name, info in manifest["files"].items():
                content = archive.extractfile(name).read()
                if len(content) != info["size"] or hashlib.sha256(content).hexdigest() != info["sha256"]:
                    raise ValueError
                output = Path(target) / name
                output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with output.open("xb") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(content)
        return manifest
    except (ValueError, KeyError, TypeError, InvalidToken, tarfile.TarError, OSError) as exc:
        raise ValidationError("Backup authentication, manifest, office binding or application master-key verification failed.") from exc


def restore(path):
    """Restore only to an empty database and empty, distinct storage directories."""
    _cipher()
    vault, exports = Path(settings.LEXFLOW_VAULT_ROOT), Path(settings.LEXFLOW_EXPORT_ROOT)
    db = settings.DATABASES["default"]
    if vault.resolve() == exports.resolve() or vault.resolve() in exports.resolve().parents or exports.resolve() in vault.resolve().parents:
        raise ValidationError("Use distinct storage roots for recovery.")
    for directory in (vault, exports):
        if directory.is_symlink() or directory.exists() and any(directory.iterdir()):
            raise ValidationError("Restore requires empty storage roots; existing data is never overwritten.")
    vendor = connection.vendor
    connection.close()
    if vendor == "sqlite":
        dbpath = Path(db["NAME"])
        if dbpath.exists():
            raise ValidationError("Restore requires a new SQLite database path.")
    elif vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'")
            if cursor.fetchone()[0]:
                raise ValidationError("Restore requires an empty PostgreSQL database.")
    else:
        raise ValidationError("Unsupported database for recovery.")
    with tempfile.TemporaryDirectory(prefix="lexflow-restore-") as temp:
        manifest = unpack(path, temp)
        if manifest["database"] != vendor:
            raise ValidationError("Restore to the same database engine as the snapshot.")
        if vendor == "sqlite":
            dbpath.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with sqlite3.connect(Path(temp) / "database.dump") as staged:
                if staged.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValidationError("The staged database failed its integrity check.")
            fd = os.open(dbpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output, (Path(temp) / "database.dump").open("rb") as source:
                shutil.copyfileobj(source, output)
        else:
            _pg(["pg_restore", "--dbname", str(db["NAME"]), "--single-transaction", "--exit-on-error",
                 "--no-owner", "--no-privileges", str(Path(temp) / "database.dump")])
        for name, destination in (("vault", vault), ("exports", exports)):
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            if (Path(temp) / name).exists():
                shutil.copytree(Path(temp) / name, destination, dirs_exist_ok=True)
        connection.close()
        office = Tenant.objects.get(uid=manifest["office"])
        audit = manifest["audit"]
        if (not verify_chain(office).ok or office.audit_seq != audit["seq"] or
                office.audit_head != audit["head"] or office.audit_seal != audit["seal"] or
                office.key_fingerprint != manifest["key_fingerprint"]):
            raise ValidationError("Recovered office failed audit/key verification. Keep it offline and investigate.")
        from apps.intake.models import Attachment
        from apps.connectors.models import Transfer
        from apps.connectors.services import checked_payload
        count = 0
        for attachment in Attachment.objects.exclude(blob_name=""):
            if hashlib.sha256(attachment.read_bytes()).hexdigest() != attachment.sha256:
                raise ValidationError("Recovered source failed decryption or checksum validation.")
            count += 1
        # Decrypt structured private fields too, not just source blobs. Successful
        # database import alone says nothing about usable recovery keys.
        from django.apps import apps
        from apps.core.fields import EncryptedTextField
        encrypted_records = 0
        for model in apps.get_models():
            names = [field.name for field in model._meta.concrete_fields if isinstance(field, EncryptedTextField)]
            if names:
                for instance in model.objects.all().iterator():
                    for name in names:
                        getattr(instance, name)
                    encrypted_records += 1
        from apps.matters.models import Matter
        from apps.connectors.services import approval_snapshot
        for matter in Matter.objects.exclude(approval_hash=""):
            approval_snapshot(matter)
        from apps.intake.models import IntakeMessage
        for message in IntakeMessage.objects.all():
            _ = message.body
            for attachment in message.attachments.all():
                _ = attachment.text
        for exported in Transfer.objects.all():
            checked_payload(exported, allow_legacy=True)
    return {"restored": True, "office": str(office.uid), "source_blobs_verified": count,
            "audit_seq": office.audit_seq, "audit_verified": True, "exports_verified": Transfer.objects.count(),
            "encrypted_records_verified": encrypted_records}
