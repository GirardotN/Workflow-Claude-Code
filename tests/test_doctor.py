import os
import unittest
from unittest import mock

from workflow_claude import doctor
from workflow_claude.doctor import FAIL, OK, WARN, collect_checks, run_doctor


class StubClient:
    """Double du client Claude pour le diagnostic (aucun processus lancé)."""

    def __init__(self, found=True, version="2.1.0 (Claude Code)", flags=None, auth=None):
        self.binary_path = "/usr/bin/claude"
        self._found, self._version, self._flags, self._auth = found, version, flags, auth

    def binary_found(self, _path):
        return self._found

    def version(self):
        return self._version

    def supported_flags(self, flags):
        return {f: (True if self._flags is None else f in self._flags) for f in flags}

    def auth_status(self):
        return self._auth


def levels(checks):
    return {label: level for level, label, _ in checks}


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.clean_env = mock.patch.dict(os.environ, {}, clear=False)
        self.clean_env.start()
        self.addCleanup(self.clean_env.stop)
        for var in doctor.detected_billing_env.__globals__["BILLING_ENV_VARS"]:
            os.environ.pop(var, None)
        self.jev = mock.patch.object(doctor, "TYPESAFE_API_KEY", "cle-de-test")
        self.jev.start()
        self.addCleanup(self.jev.stop)

    def test_all_good(self):
        checks = collect_checks(StubClient(auth={"loggedIn": True, "authMethod": "claude.ai"}))
        result = levels(checks)
        self.assertEqual(result["Claude Code CLI"], OK)
        self.assertEqual(result["Options du CLI"], OK)
        self.assertEqual(result["Session Claude"], OK)
        self.assertEqual(result["TypeSafe Jev"], OK)
        self.assertNotIn(FAIL, result.values())

    def test_missing_binary_is_blocking(self):
        checks = collect_checks(StubClient(found=False))
        self.assertEqual(levels(checks)["Claude Code CLI"], FAIL)

    def test_not_logged_in_is_blocking(self):
        checks = collect_checks(StubClient(auth={"loggedIn": False, "authMethod": "none"}))
        self.assertEqual(levels(checks)["Session Claude"], FAIL)

    def test_unknown_auth_state_is_only_a_warning(self):
        self.assertEqual(levels(collect_checks(StubClient(auth=None)))["Session Claude"], WARN)

    def test_missing_required_flag_is_blocking_and_optional_one_is_a_warning(self):
        no_tools = ["--print", "--model", "--output-format", "--permission-mode", "--no-session-persistence"]
        result = levels(collect_checks(StubClient(flags=no_tools, auth={"loggedIn": True})))
        self.assertEqual(result["Options du CLI"], FAIL)
        self.assertEqual(result["Options du CLI (facultatives)"], WARN)

    def test_api_key_in_environment_is_reported_as_removed(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret"}):
            checks = collect_checks(StubClient(auth={"loggedIn": True}))
        detail = next(d for level, label, d in checks if label == "Facturation à l'usage")
        self.assertIn("ANTHROPIC_API_KEY", detail)
        self.assertIn("retiré", detail)
        self.assertEqual(levels(checks)["Facturation à l'usage"], OK)

    def test_api_key_with_allow_flag_is_a_warning(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret"}):
            checks = collect_checks(StubClient(auth={"loggedIn": True}), allow_api_key=True)
        self.assertEqual(levels(checks)["Facturation à l'usage"], WARN)

    def test_run_doctor_exit_codes(self):
        lines = []
        self.assertEqual(run_doctor(StubClient(auth={"loggedIn": True}), out=lines.append), 0)
        self.assertEqual(run_doctor(StubClient(found=False), out=lines.append), 1)
        self.assertTrue(any("bloquant" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
