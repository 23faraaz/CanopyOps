"""Cloud transport contracts use fictional mappings, never process settings."""

import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "docker/cloud-common/connections.py"
spec = importlib.util.spec_from_file_location("cloud_connection_contract", SOURCE)
connections = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connections)


class CloudConnections(unittest.TestCase):
    def postgres_settings(self):
        return {
            "PGHOST": "test.abc123.eu-west-2.rds.amazonaws.com",
            "PGUSER": "fictional_user",
            "PGPASSWORD": "fictional_test_value",
        }

    def test_postgres_requires_ca_and_hostname(self):
        options = connections.postgres_options(self.postgres_settings())
        self.assertEqual(options["sslmode"], "verify-full")
        self.assertEqual(options["sslrootcert"], connections.RDS_CA)
        self.assertEqual(options["ssl_min_protocol_version"], "TLSv1.2")
        self.assertEqual(options["passfile"], "/dev/null")

    def test_postgres_rejects_tls_downgrade(self):
        for mode in ["disable", "prefer", "require", "verify-ca"]:
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                connections.postgres_options({**self.postgres_settings(), "PGSSLMODE": mode})

    def test_postgres_rejects_non_rds_host(self):
        for host in ["localhost", "postgres", "test.eu-west-2.rds.amazonaws.com.attacker.test"]:
            with self.subTest(host=host), self.assertRaises(ValueError):
                connections.postgres_options({**self.postgres_settings(), "PGHOST": host})

    def redis_settings(self):
        return {
            "REDIS_HOST": "fictional.cache.amazonaws.com",
            "REDIS_USERNAME": "analytics",
            "REDIS_PASSWORD": "fictional_test_value",
        }

    def test_redis_verifies_ca_and_hostname(self):
        options = connections.redis_options(self.redis_settings())
        self.assertTrue(options["ssl"])
        self.assertTrue(options["ssl_check_hostname"])
        self.assertEqual(options["ssl_cert_reqs"], "required")
        self.assertLessEqual(options["socket_timeout"], 0.2)

    def test_redis_rejects_missing_auth_or_disabled_tls(self):
        for changes in [{"REDIS_TLS": "0"}, {"REDIS_USERNAME": ""}, {"REDIS_PASSWORD": ""}]:
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError):
                connections.redis_options({**self.redis_settings(), **changes})

    def test_trust_bundle_is_the_reviewed_public_file(self):
        import hashlib

        public_bundle = SOURCE.parent / "rds-eu-west-2-bundle.crt"
        self.assertEqual(
            hashlib.sha256(public_bundle.read_bytes()).hexdigest(),
            "17c557502061c4879b844fff983288a2fb07d520f4cf2a5de60f5cda800a4494",
        )


if __name__ == "__main__":
    unittest.main()
