import os
import unittest
from unittest import mock

from workflow_claude import doctor
from workflow_claude.clients.claude_cli import BILLING_ENV_VARS
from workflow_claude.clients.jev_client import JevApiError, JevAuthError, JevConfigError, JevDecision
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


class StubJev:
    """Double du client Jev : aucun appel réseau."""

    def __init__(self, error=None):
        self.error = error

    def ping(self):
        if self.error:
            raise self.error
        return JevDecision(kind="noul", mode="live", latency_ms=312)


LOGGED_IN = {"loggedIn": True, "authMethod": "claude.ai"}


def levels(checks):
    return {label: level for level, label, _ in checks}


class TestDoctor(unittest.TestCase):
    def setUp(self):
        clean_env = mock.patch.dict(os.environ, {}, clear=False)
        clean_env.start()
        self.addCleanup(clean_env.stop)
        for var in BILLING_ENV_VARS:
            os.environ.pop(var, None)

    def checks(self, client=None, **kwargs):
        kwargs.setdefault("jev", StubJev())
        return collect_checks(client or StubClient(auth=LOGGED_IN), **kwargs)

    def test_all_good(self):
        result = levels(self.checks())
        self.assertEqual(result["Claude Code CLI"], OK)
        self.assertEqual(result["Options du CLI"], OK)
        self.assertEqual(result["Session Claude"], OK)
        self.assertEqual(result["TypeSafe Jev"], OK)
        self.assertNotIn(FAIL, result.values())

    def test_missing_binary_is_blocking(self):
        self.assertEqual(levels(self.checks(StubClient(found=False)))["Claude Code CLI"], FAIL)

    def test_not_logged_in_is_blocking(self):
        client = StubClient(auth={"loggedIn": False, "authMethod": "none"})
        self.assertEqual(levels(self.checks(client))["Session Claude"], FAIL)

    def test_unknown_auth_state_is_only_a_warning(self):
        self.assertEqual(levels(self.checks(StubClient(auth=None)))["Session Claude"], WARN)

    def test_missing_required_flag_is_blocking_and_optional_one_is_a_warning(self):
        no_tools = ["--print", "--model", "--output-format", "--permission-mode", "--no-session-persistence"]
        result = levels(self.checks(StubClient(flags=no_tools, auth=LOGGED_IN)))
        self.assertEqual(result["Options du CLI"], FAIL)
        self.assertEqual(result["Options du CLI (facultatives)"], WARN)

    def test_api_key_in_environment_is_reported_as_removed(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret"}):
            checks = self.checks()
        detail = next(d for level, label, d in checks if label == "Facturation à l'usage")
        self.assertIn("ANTHROPIC_API_KEY", detail)
        self.assertIn("retiré", detail)
        self.assertEqual(levels(checks)["Facturation à l'usage"], OK)

    def test_api_key_with_allow_flag_is_a_warning(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret"}):
            checks = self.checks(allow_api_key=True)
        self.assertEqual(levels(checks)["Facturation à l'usage"], WARN)

    def test_run_doctor_exit_codes(self):
        lines = []
        self.assertEqual(run_doctor(StubClient(auth=LOGGED_IN), out=lines.append, jev=StubJev()), 0)
        self.assertEqual(run_doctor(StubClient(found=False), out=lines.append, jev=StubJev()), 1)
        self.assertTrue(any("bloquant" in line for line in lines))


class TestDoctorJev(unittest.TestCase):
    def jev_level(self, **kwargs):
        checks = collect_checks(StubClient(auth=LOGGED_IN), **kwargs)
        return next((level, detail) for level, label, detail in checks if label == "TypeSafe Jev")

    def test_valid_key_reports_latency(self):
        level, detail = self.jev_level(jev=StubJev())
        self.assertEqual(level, OK)
        self.assertIn("312 ms", detail)

    def test_rejected_key_is_blocking(self):
        level, detail = self.jev_level(jev=StubJev(JevAuthError("Clé TypeSafe refusée (HTTP 401)")))
        self.assertEqual(level, FAIL)
        self.assertIn("refusée", detail)

    def test_unreachable_service_is_only_a_warning(self):
        level, _ = self.jev_level(jev=StubJev(JevApiError("Timeout")))
        self.assertEqual(level, WARN)

    def test_missing_key_is_blocking_without_mock(self):
        with mock.patch.object(doctor, "JevClient", side_effect=JevConfigError("Clé TypeSafe absente ... --mock ...")):
            level, detail = self.jev_level()
        self.assertEqual(level, FAIL)
        self.assertIn("--mock", detail)

    def test_mock_mode_is_a_visible_warning_and_makes_no_call(self):
        stub = mock.Mock()
        level, detail = self.jev_level(mock=True, jev=stub)
        self.assertEqual(level, WARN)
        self.assertIn("SIMULATION", detail)
        stub.ping.assert_not_called()


if __name__ == "__main__":
    unittest.main()
