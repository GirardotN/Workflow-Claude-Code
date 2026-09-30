"""
Tests du client Jev contre un faux serveur au format réel de l'API : schéma, fail-closed,
erreurs typées, reprises, plafond de taille, confidentialité de la clé.
"""

import logging
import unittest
from unittest import mock

import requests
from fake_jev import AUTH_ERROR_401, MAX_TOKENS_400, FakeJevServer, error, ok_choice, ok_noul

from workflow_claude.clients import jev_client
from workflow_claude.clients.jev_client import JevApiError, JevAuthError, JevClient, JevConfigError

SECRET_KEY = "tsk-SECRET-KEY-0123456789-abcdefghijklmnop"
CHOICES = ["Tâche Simple", "Tâche Moyenne", "Tâche Complexe"]


class JevServerTestCase(unittest.TestCase):
    def setUp(self):
        self.server = FakeJevServer().start()
        self.addCleanup(self.server.stop)
        self.client = JevClient(api_key=SECRET_KEY, api_url=self.server.url, timeout_seconds=5, max_retries=2, mock_mode=False)
        sleeper = mock.patch.object(jev_client.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)


class TestRequestFormat(JevServerTestCase):
    def test_choice_request_and_response(self):
        self.server.script(ok_choice("Tâche Moyenne", 0.81))

        choice = self.client.classify("Contexte de la tâche", CHOICES, "Quel niveau ?", descriptions={"Tâche Simple": "petit"})

        self.assertEqual(choice, "Tâche Moyenne")
        request = self.server.requests[0]
        self.assertEqual(request["path"], "/v1/systemone")
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {SECRET_KEY}")
        body = request["body"]
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["state"], "Contexte de la tâche")
        question = body["questions"]["selection"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(question["instructions"], "Quel niveau ?")
        self.assertEqual(question["criteria"]["Tâche Simple"], "petit")          # description fournie
        self.assertEqual(question["criteria"]["Tâche Complexe"], "Option: Tâche Complexe")  # repli

        decision = self.client.last_decision
        self.assertEqual((decision.kind, decision.mode, decision.result), ("choice", "live", "Tâche Moyenne"))
        self.assertAlmostEqual(decision.value, 0.81)
        self.assertEqual(decision.input_tokens, 300 + 77)
        self.assertGreaterEqual(decision.latency_ms, 0)

    def test_validate_request_shape(self):
        self.server.script(ok_noul(0.87))
        self.assertTrue(self.client.validate("Diff + review", "Le code est-il sûr ?"))
        question = self.server.requests[0]["body"]["questions"]["is_valid"]
        self.assertEqual(question, {"type": "noul", "instructions": "Le code est-il sûr ?"})

    def test_no_request_goes_to_a_decide_endpoint(self):
        self.server.script(ok_noul(0.9))
        self.client.validate("x", "y")
        self.assertTrue(all(r["path"] == "/v1/systemone" for r in self.server.requests))


class TestValidationSemantics(JevServerTestCase):
    def test_threshold_comparison(self):
        for noul, expected in ((0.01, False), (0.49, False), (0.5, True), (0.61, True), (0.99, True)):
            self.server.script(ok_noul(noul))
            self.assertEqual(self.client.validate("c", "q"), expected, noul)

    def test_custom_threshold(self):
        self.server.script(ok_noul(0.61))
        self.assertFalse(self.client.validate("c", "q", threshold=0.8))
        self.assertEqual(self.client.last_decision.threshold, 0.8)

    def test_decision_value_is_recorded(self):
        self.server.script(ok_noul(0.42))
        self.client.validate("c", "q")
        decision = self.client.last_decision
        self.assertEqual((decision.kind, decision.result, decision.value), ("noul", False, 0.42))


class TestFailClosed(JevServerTestCase):
    """Régression : avec le format réel, l'ancien client lisait `results` (absent) et validait TOUT par défaut."""

    def test_old_schema_without_answers_is_an_error_not_a_validation(self):
        self.server.script({"status": 200, "body": {"results": {"is_valid": {"probability": 0.99}}}})
        with self.assertRaises(JevApiError) as ctx:
            self.client.validate("c", "q")
        self.assertIn("answers", str(ctx.exception))

    def test_missing_noul_field_is_an_error(self):
        self.server.script({"status": 200, "body": {"answers": {"is_valid": {"type": "noul"}}}})
        with self.assertRaises(JevApiError):
            self.client.validate("c", "q")

    def test_missing_question_key_is_an_error(self):
        self.server.script(ok_noul(0.9, key="autre_cle"))
        with self.assertRaises(JevApiError):
            self.client.validate("c", "q")

    def test_out_of_range_or_wrong_type_noul_is_an_error(self):
        for bad in (1.5, -0.1, "0.9", True, None):
            self.server.script({"status": 200, "body": {"answers": {"is_valid": {"type": "noul", "noul": bad}}}})
            with self.assertRaises(JevApiError, msg=repr(bad)):
                self.client.validate("c", "q")

    def test_choice_outside_the_options_is_an_error(self):
        self.server.script(ok_choice("Tâche Inconnue"))
        with self.assertRaises(JevApiError):
            self.client.classify("c", CHOICES)

    def test_invalid_json_body_is_an_error(self):
        self.server.script({"status": 200, "raw": "<html>pas du json</html>"})
        with self.assertRaises(JevApiError) as ctx:
            self.client.validate("c", "q")
        self.assertIn("JSON", str(ctx.exception))

    def test_non_dict_json_is_an_error(self):
        self.server.script({"status": 200, "body": [1, 2, 3]})
        with self.assertRaises(JevApiError):
            self.client.validate("c", "q")


class TestHttpErrors(JevServerTestCase):
    def test_authentication_errors_are_typed_and_not_retried(self):
        self.server.script(AUTH_ERROR_401)
        with self.assertRaises(JevAuthError) as ctx:
            self.client.validate("c", "q")
        self.assertIn("TYPESAFE_API_KEY", str(ctx.exception))
        self.assertEqual(len(self.server.requests), 1)

        self.server.requests.clear()
        self.server.script(error(403, {"error_type": "authentication_error", "message": "Must supply an API key!"}))
        with self.assertRaises(JevAuthError):
            self.client.validate("c", "q")

    def test_size_limit_error_is_explicit_and_not_retried(self):
        self.server.script(MAX_TOKENS_400)
        with self.assertRaises(JevApiError) as ctx:
            self.client.validate("c", "q")
        self.assertIn("max_tokens_exceeded", str(ctx.exception))
        self.assertEqual(len(self.server.requests), 1)

    def test_schema_error_422_is_readable(self):
        self.server.script(error(422, [{"type": "missing", "loc": ["body", "questions"], "msg": "Field required"}]))
        with self.assertRaises(JevApiError) as ctx:
            self.client.validate("c", "q")
        self.assertIn("Field required", str(ctx.exception))
        self.assertIn("body.questions", str(ctx.exception))

    def test_server_error_is_retried_then_succeeds(self):
        self.server.script(error(503, "unavailable"), error(500, "boom"), ok_noul(0.9))
        self.assertTrue(self.client.validate("c", "q"))
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(self.client.last_decision.attempts, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_persistent_server_error_aborts_instead_of_validating(self):
        self.server.script(error(500, "boom"))
        with self.assertRaises(JevApiError) as ctx:
            self.client.validate("c", "q")
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertEqual(len(self.server.requests), 3)  # 1 essai + 2 reprises

    def test_rate_limit_honors_retry_after(self):
        self.server.script(error(429, "slow down", headers={"Retry-After": "7"}), ok_noul(0.9))
        self.assertTrue(self.client.validate("c", "q"))
        self.sleep.assert_called_once_with(7.0)

    def test_retry_after_is_capped(self):
        self.server.script(error(429, "slow down", headers={"Retry-After": "9999"}), ok_noul(0.9))
        self.client.validate("c", "q")
        self.sleep.assert_called_once_with(30.0)

    def test_timeout_is_retried_then_aborts(self):
        client = JevClient(api_key=SECRET_KEY, api_url=self.server.url, timeout_seconds=0.2, max_retries=1, mock_mode=False)
        self.server.script({**ok_noul(0.9), "sleep": 0.6})
        with self.assertRaises(JevApiError) as ctx:
            client.validate("c", "q")
        self.assertIn("Timeout", str(ctx.exception))

    def test_connection_refused_is_an_api_error(self):
        server = FakeJevServer().start()
        url = server.url
        server.stop()
        client = JevClient(api_key=SECRET_KEY, api_url=url, timeout_seconds=2, max_retries=0, mock_mode=False)
        with self.assertRaises(JevApiError):
            client.validate("c", "q")


class TestSizeLimit(JevServerTestCase):
    def test_oversized_state_is_truncated_before_sending(self):
        client = JevClient(api_key=SECRET_KEY, api_url=self.server.url, max_state_chars=5000, mock_mode=False)
        self.server.script(ok_noul(0.9))
        client.validate("A" * 4000 + "MILIEU" + "B" * 40000 + "FIN", "q")
        sent = self.server.requests[0]["body"]["state"]
        self.assertLessEqual(len(sent), 5000)
        self.assertIn("caractères omis", sent)
        self.assertTrue(sent.startswith("AAAA"))
        self.assertTrue(sent.endswith("FIN"))
        self.assertTrue(client.last_decision.truncated)

    def test_small_state_is_untouched(self):
        self.server.script(ok_noul(0.9))
        self.client.validate("petit contexte", "q")
        self.assertEqual(self.server.requests[0]["body"]["state"], "petit contexte")
        self.assertFalse(self.client.last_decision.truncated)


class TestKeyConfidentiality(JevServerTestCase):
    def test_key_never_appears_in_errors_or_logs(self):
        scenarios = (
            AUTH_ERROR_401,
            error(400, {"error_type": "api_usage_error", "message": f"echo {SECRET_KEY}"}),
            error(500, f"boom {SECRET_KEY}"),
        )
        with self.assertLogs("jev_client", level=logging.DEBUG) as logs:
            logging.getLogger("jev_client").debug("init")
            for scenario in scenarios:
                self.server.script(scenario)
                with self.assertRaises(JevApiError) as ctx:
                    self.client.validate("c", "q")
                self.assertNotIn(SECRET_KEY, str(ctx.exception))
        self.assertNotIn(SECRET_KEY, "\n".join(logs.output))

    def test_network_error_message_has_no_key(self):
        with mock.patch.object(self.client._session, "post", side_effect=requests.ConnectionError(f"https://x/?k={SECRET_KEY}")):
            with self.assertRaises(JevApiError) as ctx:
                self.client.validate("c", "q")
        self.assertNotIn(SECRET_KEY, str(ctx.exception))


class TestConfiguration(unittest.TestCase):
    def test_missing_key_refuses_to_start(self):
        for missing in ("", "   ", "votre_cle_typesafe_ici"):
            with self.assertRaises(JevConfigError, msg=repr(missing)) as ctx:
                JevClient(api_key=missing, mock_mode=False)
            self.assertIn("--mock", str(ctx.exception))

    def test_config_error_is_an_api_error_subclass(self):
        self.assertTrue(issubclass(JevConfigError, JevApiError))

    def test_mock_mode_needs_no_key_and_is_labeled(self):
        client = JevClient(api_key="", mock_mode=True)
        self.assertEqual(client.mode, "mock")
        self.assertEqual(client.classify("Petite tâche", CHOICES), "Tâche Simple")
        self.assertTrue(client.validate("Aucun bug détecté", "q"))
        self.assertEqual(client.last_decision.mode, "mock")

    def test_live_mode_label(self):
        self.assertEqual(JevClient(api_key=SECRET_KEY, mock_mode=False).mode, "live")

    def test_no_silent_fallback_to_mock(self):
        """Avant : sans clé, le client basculait en mock sans rien dire (validations non fiables)."""
        with self.assertRaises(JevConfigError):
            JevClient(api_key="", mock_mode=False)


class TestPing(JevServerTestCase):
    def test_ping_returns_a_decision(self):
        self.server.script(ok_noul(0.97))
        decision = self.client.ping()
        self.assertEqual(decision.mode, "live")
        self.assertGreaterEqual(decision.latency_ms, 0)

    def test_ping_propagates_auth_errors(self):
        self.server.script(AUTH_ERROR_401)
        with self.assertRaises(JevAuthError):
            self.client.ping()


if __name__ == "__main__":
    unittest.main()
