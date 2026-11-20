"""Offline guards only; these tests never open credentials or database backups."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from postgres_rehearsal import restore_images, verify_versions  # noqa: E402

CLIENT = "postgres:17.12-bookworm@sha256:" + "a" * 64
TARGET = "postgres:17.11-bookworm@sha256:" + "b" * 64


class RestoreVersionGuards(unittest.TestCase):
    def test_default_preserves_existing_client_and_server(self):
        self.assertEqual(restore_images(CLIENT), (CLIENT, CLIENT))

    def test_candidate_changes_only_target(self):
        self.assertEqual(restore_images(CLIENT, TARGET), (CLIENT, TARGET))

    def test_mutable_or_untrusted_images_rejected(self):
        for image in [
            "postgres:17.11-bookworm",
            "other/postgres:17.11-bookworm@sha256:" + "b" * 64,
            "postgres:latest",
            "postgres:17.11-alpine@sha256:" + "b" * 64,
        ]:
            with self.subTest(image=image), self.assertRaises(ValueError):
                restore_images(CLIENT, image)

    def test_major_change_requires_separate_rehearsal(self):
        with self.assertRaises(ValueError):
            restore_images(CLIENT, TARGET.replace("17.11", "18.1"))

    def test_actual_minor_difference_is_reported(self):
        result = verify_versions("17.12 (Debian fixture)", "170011", CLIENT, TARGET)
        self.assertTrue(result["same_major"])
        self.assertTrue(result["target_older_minor"])
        self.assertEqual(result["target_version"], "17.11")

    def test_wrong_running_target_rejected(self):
        with self.assertRaises(ValueError):
            verify_versions("17.12", "170012", CLIENT, TARGET)

    def test_wrong_source_major_rejected(self):
        with self.assertRaises(ValueError):
            verify_versions("18.1", "170011", CLIENT, TARGET)

    def test_malformed_source_version_rejected(self):
        with self.assertRaises(ValueError):
            verify_versions("unknown", "170011", CLIENT, TARGET)


if __name__ == "__main__":
    unittest.main()
