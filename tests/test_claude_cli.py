"""
Tests du client Claude CLI contre un faux binaire `claude` (tests/fake_claude.py) :
prompt sur stdin, environnement nettoyé, erreurs typées, reprises, délais, résolution du binaire.
"""

import hashlib
import json
import logging
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workflow_claude.clients import claude_cli
from workflow_claude.clients.claude_cli import (
    ClaudeAuthError,
    ClaudeCliClient,
    ClaudeCliError,
    ClaudeQuotaError,
    ClaudeTimeoutError,
    _assert_cmd_safe,
    detected_billing_env,
    resolve_binary,
    sanitized_env,
)
from workflow_claude.roles import Role

FAKE_SCRIPT = Path(__file__).with_name("fake_claude.py")


def make_fake_binary(directory: Path) -> str:
    """Crée un exécutable `claude` qui lance fake_claude.py (shim .cmd sous Windows, script sinon)."""
    if os.name == "nt":
        shim = directory / "claude.cmd"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{FAKE_SCRIPT}" %*\r\n', encoding="utf-8", newline="")
    else:
        shim = directory / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SCRIPT}" "$@"\n', encoding="utf-8", newline="\n")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim)


class FakeClaudeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name).resolve()
        self.binary = make_fake_binary(self.dir)
        self.log_path = self.dir / "calls.jsonl"

        self.env_patch = mock.patch.dict(os.environ, {"FAKE_CLAUDE_LOG": str(self.log_path)})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        for var in ("FAKE_CLAUDE_MODE", "FAKE_CLAUDE_MESSAGE", "FAKE_CLAUDE_STATE", "FAKE_CLAUDE_FAILS", "FAKE_CLAUDE_SLEEP"):
            os.environ.pop(var, None)

        self.client = ClaudeCliClient(binary_path=self.binary, timeout_seconds=30, mock_mode=False, max_retries=2)

    def mode(self, mode, message=None, **extra):
        os.environ["FAKE_CLAUDE_MODE"] = mode
        if message is not None:
            os.environ["FAKE_CLAUDE_MESSAGE"] = message
        for key, value in extra.items():
            os.environ[f"FAKE_CLAUDE_{key.upper()}"] = str(value)

    def calls(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestPromptOnStdin(FakeClaudeTestCase):
    def test_huge_prompt_with_shell_metacharacters_arrives_intact_on_stdin(self):
        prompt = ("Ligne avec & | % ^ \" ' < > `backticks` $(whoami) ; rm -rf / \n" * 4000) + "FIN"
        self.assertGreater(len(prompt), 200_000)  # très au-delà de la limite de ligne de commande Windows

        result = self.client.run(prompt, model="sonnet")

        self.assertTrue(result.startswith("OK:"))
        call = self.calls()[0]
        self.assertEqual(call["stdin_len"], len(prompt))
        self.assertEqual(call["stdin_sha256"], hashlib.sha256(prompt.encode("utf-8")).hexdigest())
        # le contenu du prompt n'apparaît dans AUCUN argument
        self.assertFalse(any("rm -rf" in arg or "whoami" in arg for arg in call["argv"]))

    def test_command_has_no_prompt_and_expected_flags(self):
        cmd = self.client.build_command("opus", tools="", permission_mode="acceptEdits")
        self.assertIn("-p", cmd)
        self.assertIn("--no-session-persistence", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "json")
        self.assertIn("--tools=", cmd)                      # forme collée : "" = aucun outil
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")

    def test_tool_lists_use_the_attached_form_to_avoid_variadic_capture(self):
        cmd = self.client.build_command(
            "sonnet",
            tools="Read,Edit",
            allowed_tools=["Bash(pytest:*)", "Bash(ls:*)"],
            disallowed_tools=["Bash(git push:*)"],
        )
        self.assertIn("--tools=Read,Edit", cmd)
        self.assertIn("--allowedTools=Bash(pytest:*),Bash(ls:*)", cmd)
        self.assertIn("--disallowedTools=Bash(git push:*)", cmd)

    def test_options_reach_the_process_and_cwd_is_respected(self):
        workdir = self.dir / "work"
        workdir.mkdir()
        self.client.run("salut", model="haiku", cwd=str(workdir), tools="Read,Grep", permission_mode="plan")
        call = self.calls()[0]
        self.assertEqual(Path(call["cwd"]).resolve(), workdir.resolve())
        self.assertIn("--tools=Read,Grep", call["argv"])
        self.assertIn("plan", call["argv"])


class TestEnvironmentSanitizing(FakeClaudeTestCase):
    def test_billing_variables_are_removed_by_default(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret", "ANTHROPIC_BASE_URL": "https://proxy.example"}):
            self.assertIn("ANTHROPIC_API_KEY", detected_billing_env())
            self.client.run("x", model="sonnet")
        call = self.calls()[0]
        self.assertFalse(call["has_api_key"])
        self.assertFalse(call["has_base_url"])

    def test_allow_api_key_keeps_them(self):
        client = ClaudeCliClient(binary_path=self.binary, mock_mode=False, allow_api_key=True)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret"}):
            client.run("x", model="sonnet")
        self.assertTrue(self.calls()[0]["has_api_key"])

    def test_sanitized_env_leaves_other_variables_alone(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk", "MY_OTHER_VAR": "keep"}):
            env = sanitized_env(allow_api_key=False)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["MY_OTHER_VAR"], "keep")


class TestErrors(FakeClaudeTestCase):
    def test_not_logged_in_is_an_auth_error_even_with_exit_code_1(self):
        self.mode("error", "Not logged in · Please run /login")
        with self.assertRaises(ClaudeAuthError) as ctx:
            self.client.run("x", model="sonnet")
        self.assertIn("claude auth login", str(ctx.exception))

    def test_usage_limit_is_a_quota_error_and_is_not_retried(self):
        self.mode("error", "You've hit your limit · resets 5pm (Europe/Paris)")
        with self.assertRaises(ClaudeQuotaError) as ctx:
            self.client.run("x", model="opus")
        self.assertIn("resets 5pm", str(ctx.exception))
        self.assertEqual(len(self.calls()), 1)

    def test_error_flag_is_honored_even_when_exit_code_is_zero(self):
        self.mode("error_exit0", "Not logged in · Please run /login")
        with self.assertRaises(ClaudeAuthError):
            self.client.run("x", model="sonnet")

    def test_generic_error_is_a_plain_cli_error(self):
        self.mode("error", "Quelque chose d'inattendu")
        with self.assertRaises(ClaudeCliError) as ctx:
            self.client.run("x", model="sonnet")
        self.assertNotIsInstance(ctx.exception, (ClaudeAuthError, ClaudeQuotaError, ClaudeTimeoutError))

    def test_error_text_is_never_returned_as_a_valid_result(self):
        """Régression : une erreur « usage limit reached » était renvoyée comme du texte valide."""
        self.mode("error_exit0", "Claude usage limit reached")
        with self.assertRaises(ClaudeCliError):
            self.client.run("x", model="sonnet")

    def test_missing_binary(self):
        client = ClaudeCliClient(binary_path=str(self.dir / "nope" / "claude"), mock_mode=False)
        with self.assertRaises(ClaudeCliError) as ctx:
            client.run("x", model="sonnet")
        self.assertIn("introuvable", str(ctx.exception))


class TestRetriesAndTimeouts(FakeClaudeTestCase):
    def test_transient_error_is_retried_then_succeeds(self):
        self.mode("flaky", state=self.dir / "state.txt", fails=1)
        with mock.patch.object(claude_cli.time, "sleep") as sleep:
            result = self.client.run("x", model="sonnet")
        self.assertEqual(result, "OK après reprise")
        self.assertEqual(len(self.calls()), 2)
        sleep.assert_called_once()

    def test_transient_error_gives_up_after_max_retries(self):
        self.mode("flaky", state=self.dir / "state.txt", fails=99)
        with mock.patch.object(claude_cli.time, "sleep"):
            with self.assertRaises(ClaudeCliError):
                self.client.run("x", model="sonnet")
        self.assertEqual(len(self.calls()), 3)  # 1 essai + 2 reprises

    def test_timeout_raises_a_typed_error_and_is_not_retried(self):
        self.mode("sleep", sleep=4)
        with self.assertRaises(ClaudeTimeoutError):
            self.client.run("x", model="sonnet", timeout=1)
        self.assertEqual(len(self.calls()), 1)

    def test_timeouts_depend_on_the_role(self):
        self.assertGreater(self.client.timeout_for(Role.DEV), self.client.timeout_for(Role.SPEC))
        self.assertGreater(self.client.timeout_for(Role.SPEC), self.client.timeout_for(Role.QUALITY))
        self.assertEqual(self.client.timeout_for(Role.QUALITY), self.client.timeout_seconds)
        self.assertEqual(self.client.timeout_for(None), self.client.timeout_seconds)


class TestResultParsing(FakeClaudeTestCase):
    def test_metadata_is_exposed_in_last_result(self):
        self.client.run("x", model="sonnet")
        result = self.client.last_result
        self.assertEqual(result.cost_usd, 0.0042)
        self.assertEqual(result.num_turns, 3)
        self.assertEqual(result.session_id, "sess-123")
        self.assertEqual(result.duration_ms, 1234)
        self.assertFalse(result.is_error)

    def test_permission_denials_are_logged(self):
        self.mode("denials")
        with self.assertLogs("claude_cli", level=logging.WARNING) as logs:
            self.assertEqual(self.client.run("x", model="sonnet"), "fait")
        self.assertIn("Bash", "\n".join(logs.output))
        self.assertEqual(self.client.last_result.permission_denials[0]["tool_name"], "Bash")

    def test_plain_text_output_is_returned_as_is(self):
        self.mode("plain", "juste du texte")
        self.assertEqual(self.client.run("x", model="sonnet"), "juste du texte")

    def test_nested_braces_in_result(self):
        client = ClaudeCliClient(mock_mode=False)
        raw = 'logs\n{"type":"result","is_error":false,"result":"function f() { return { a: { b: 1 } }; }"}\nfin'
        self.assertEqual(client._extract_result(raw), "function f() { return { a: { b: 1 } }; }")


class TestBinaryResolution(unittest.TestCase):
    def test_windows_shim_resolves_to_the_native_binary_without_cmd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "claude.cmd").write_text("@echo off\n", encoding="utf-8")
            native = root / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            native.parent.mkdir(parents=True)
            native.write_bytes(b"MZ")
            cmd = resolve_binary(str(root / "claude.cmd"))
            if os.name == "nt":
                self.assertEqual(cmd, [str(native)])
            else:
                self.assertEqual(cmd, [str(root / "claude.cmd")])

    def test_windows_shim_without_native_binary_falls_back_to_cmd(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = str(Path(tmp) / "claude.cmd")
            cmd = resolve_binary(shim)
            if os.name == "nt":
                self.assertEqual(cmd[1:], ["/d", "/c", shim])
            else:
                self.assertEqual(cmd, [shim])

    def test_cmd_fallback_refuses_arguments_cmd_would_interpret(self):
        for bad in ("a&b", "a|b", "a^b", "50%", 'say "hi"', "line\nbreak", "a<b", "a>b"):
            with self.assertRaises(ClaudeCliError, msg=bad):
                _assert_cmd_safe(["--model", bad])
        _assert_cmd_safe(["--tools=Read,Edit", "--allowedTools=Bash(pytest:*),Bash(git status:*)"])  # sans danger


class TestDiagnosticHelpers(FakeClaudeTestCase):
    def test_version(self):
        self.assertIn("9.9.9", self.client.version())
        self.assertIsNone(ClaudeCliClient(binary_path=str(self.dir / "nope"), mock_mode=False).version())

    def test_binary_found(self):
        self.assertTrue(ClaudeCliClient.binary_found(self.binary))
        self.assertFalse(ClaudeCliClient.binary_found(str(self.dir / "absent")))


if __name__ == "__main__":
    unittest.main()
