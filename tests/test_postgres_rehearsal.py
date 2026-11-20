"""Offline guards only; these tests never open credentials or database backups."""

from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from postgres_rehearsal import (
    capture_restore_command,
    restore_configuration,
    restore_images,
    verify_versions,
)  # noqa: E402

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


class RestoreConfigurationGuards(unittest.TestCase):
    def test_tools_profile_client_is_included_for_validation(self):
        def compose(*arguments):
            services = {"postgres": {"image": TARGET}}
            if arguments[:2] == ("--profile", "tools"):
                services["restore-client"] = {"image": CLIENT}
            return json.dumps({"services": services})

        config = restore_configuration(compose, CLIENT, TARGET)
        self.assertIn("restore-client", config["services"])

    def test_missing_client_fails_with_configuration_error_not_keyerror(self):
        def compose(*arguments):
            return json.dumps({"services": {"postgres": {"image": TARGET}}})

        with self.assertRaisesRegex(ValueError, "tools-profile client"):
            restore_configuration(compose, CLIENT, TARGET)

    def test_wrong_server_or_client_is_rejected(self):
        for server, client in [(CLIENT, CLIENT), (TARGET, TARGET)]:
            with self.subTest(server=server, client=client):

                def compose(*arguments):
                    return json.dumps(
                        {
                            "services": {
                                "postgres": {"image": server},
                                "restore-client": {"image": client},
                            }
                        }
                    )

                with self.assertRaisesRegex(ValueError, "image guard"):
                    restore_configuration(compose, CLIENT, TARGET)


class RestoreCommandOutput(unittest.TestCase):
    def command(self, body):
        with tempfile.TemporaryDirectory(prefix="canopy-output-contract-") as directory:
            return capture_restore_command([sys.executable, "-c", body], directory)

    def test_warning_does_not_corrupt_numeric_version(self):
        result = self.command(
            "import sys; sys.stderr.write('fictional Compose warning\\n'); print('170011')"
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("warning", result.stderr)
        checked = verify_versions("17.12", result.stdout.strip(), CLIENT, TARGET)
        self.assertEqual(checked["target_server_version_num"], 170011)

    def test_warning_does_not_corrupt_json(self):
        result = self.command(
            "import sys; sys.stderr.write('fictional Compose warning\\n'); print('{\"ready\":true}')"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"ready": True})
        self.assertNotIn("warning", result.stdout)

    def test_failed_command_keeps_separate_diagnostics(self):
        result = self.command(
            "import sys; print('fictional output'); sys.stderr.write('fictional failure'); sys.exit(7)"
        )
        self.assertEqual(result.returncode, 7)
        self.assertIn("output", result.stdout)
        self.assertIn("failure", result.stderr)

    def test_non_numeric_stdout_is_still_rejected(self):
        with self.assertRaises(ValueError):
            verify_versions("17.12", "unexpected output\n170011", CLIENT, TARGET)


if __name__ == "__main__":
    unittest.main()
