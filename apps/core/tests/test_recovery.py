import io
import json
from pathlib import Path
import tarfile
import tempfile

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TransactionTestCase, override_settings

from apps.core import recovery
from apps.core.testing import make_office, property_matter, reset_vault


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RecoveryControlsTests(TransactionTestCase):
    def setUp(self):
        reset_vault()
        self.temp = tempfile.TemporaryDirectory(prefix="lexflow-recovery-test-")
        self.addCleanup(self.temp.cleanup)
        self.key = Fernet.generate_key().decode()
        self.backup = Path(self.temp.name) / "office.lfxbackup"
        config = override_settings(LEXFLOW_MAINTENANCE=True, LEXFLOW_BACKUP_KEY=self.key)
        config.enable()
        self.addCleanup(config.disable)
        self.office, self.users = make_office()
        property_matter(self.office, blatt_contract="812")

    def test_snapshot_authenticates_database_sources_and_external_audit_checkpoint(self):
        report = recovery.create(self.backup)
        self.assertEqual(report["office"], str(self.office.uid))
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(b"Anna Schmidt", self.backup.read_bytes())
        manifest = recovery.unpack(self.backup, Path(self.temp.name) / "unpack")
        self.assertIn("database.dump", manifest["files"])
        self.assertTrue(any(key.startswith("vault/") for key in manifest["files"]))
        self.assertGreater(manifest["audit"]["seq"], 0)

    def test_wrong_backup_key_and_corrupt_bytes_are_rejected(self):
        recovery.create(self.backup)
        with override_settings(LEXFLOW_BACKUP_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(ValidationError):
                recovery.unpack(self.backup, Path(self.temp.name) / "wrong-key")
        encoded = self.backup.read_bytes()
        self.backup.write_bytes(encoded[:-30] + b"corrupt")
        with self.assertRaises(ValidationError):
            recovery.unpack(self.backup, Path(self.temp.name) / "corrupt")

    def test_wrong_application_master_key_is_rejected_before_restore(self):
        recovery.create(self.backup)
        with override_settings(LEXFLOW_MASTER_KEY="fernet:" + Fernet.generate_key().decode()):
            with self.assertRaises(ValidationError):
                recovery.unpack(self.backup, Path(self.temp.name) / "wrong-master")

    def test_nonmaintenance_backup_and_overwrite_are_rejected(self):
        with override_settings(LEXFLOW_MAINTENANCE=False):
            with self.assertRaises(ValidationError):
                recovery.create(self.backup)
        recovery.create(self.backup)
        with self.assertRaises(ValidationError):
            recovery.create(self.backup)

    def test_authenticated_archive_cannot_escape_restore_directory(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as handle:
            data = b"outside"
            info = tarfile.TarInfo("../outside")
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
        self.backup.write_bytes(recovery.MAGIC + Fernet(self.key.encode()).encrypt(archive.getvalue()))
        with self.assertRaises(ValidationError):
            recovery.unpack(self.backup, Path(self.temp.name) / "unsafe")
        self.assertFalse((Path(self.temp.name) / "outside").exists())

    def test_maintenance_blocks_web_writes(self):
        response = self.client.post("/api/v1/intake/", {"subject": "test"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "60")
