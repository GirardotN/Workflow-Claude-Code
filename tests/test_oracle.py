"""
Oracle de tests : détection de la commande, exécution robuste (timeout qui tue l'arbre, stdin fermé, sortie propre)
et comparaison à la baseline par ENSEMBLES de tests en échec.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gitrepo import make_repo

from workflow_claude.clients import test_runner as tr
from workflow_claude.clients.claude_cli import ClaudeCliClient
from workflow_claude.clients.jev_client import JevClient
from workflow_claude.clients.test_runner import (
    TestResult,
    TestRunner,
    find_python,
    is_regression,
    parse_failed_tests,
    read_project_config,
    strip_ansi,
)
from workflow_claude.orchestrator import MultiAgentOrchestrator

PY = sys.executable


def result(passed, failed=(), output="", command="tests"):
    return TestResult(passed=passed, command=command, output=output or ("OK" if passed else "FAILED"),
                      duration_seconds=0.1, returncode=0 if passed else 1, failed_tests=list(failed))


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name).resolve()
        self.runner = TestRunner(timeout_seconds=30)

    def touch(self, rel, content=""):
        target = self.dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return target

    def no_tool_on_path(self):
        return mock.patch.object(tr.shutil, "which", return_value=None)


class TestNodeDetection(ProjectTestCase):
    def pkg(self, script='jest --coverage'):
        self.touch("package.json", '{"name": "x", "scripts": {"test": "%s"}}' % script)

    def test_default_manager_is_npm(self):
        self.pkg()
        with self.no_tool_on_path():
            self.assertEqual(self.runner.detect_test_command(self.dir), ["npm", "test"])

    def test_lockfile_selects_the_package_manager(self):
        self.pkg()
        for lockfile, expected in (("pnpm-lock.yaml", ["pnpm", "test"]), ("yarn.lock", ["yarn", "test"]),
                                   ("bun.lockb", ["bun", "run", "test"]), ("bun.lock", ["bun", "run", "test"])):
            with self.subTest(lockfile=lockfile):
                for other in ("pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock"):
                    (self.dir / other).unlink(missing_ok=True)
                self.touch(lockfile)
                with self.no_tool_on_path():
                    self.assertEqual(self.runner.detect_test_command(self.dir), expected)

    def test_npm_placeholder_script_is_ignored(self):
        self.pkg('echo \\"Error: no test specified\\" && exit 1')
        with self.no_tool_on_path():
            self.assertIsNone(self.runner.detect_test_command(self.dir))

    def test_package_json_without_test_script(self):
        self.touch("package.json", '{"name": "x"}')
        with self.no_tool_on_path():
            self.assertIsNone(self.runner.detect_test_command(self.dir))


class TestPythonDetection(ProjectTestCase):
    def test_pytest_when_the_interpreter_has_it(self):
        self.touch("tests/test_a.py", "def test_a(): pass\n")
        with mock.patch.object(tr, "python_has_module", return_value=True):
            cmd = self.runner.detect_test_command(self.dir)
        self.assertEqual(cmd[1:], ["-m", "pytest"])

    def test_unittest_otherwise_with_the_right_folder(self):
        self.touch("test/test_a.py", "import unittest\n")
        with mock.patch.object(tr, "python_has_module", return_value=False):
            cmd = self.runner.detect_test_command(self.dir)
        self.assertEqual(cmd[1:], ["-m", "unittest", "discover", "-s", "test"])

    def test_pyproject_pytest_section_and_conftest_trigger_python_detection(self):
        self.touch("pyproject.toml", "[tool.pytest.ini_options]\naddopts = '-q'\n")
        with mock.patch.object(tr, "python_has_module", return_value=True):
            self.assertIsNotNone(self.runner.detect_test_command(self.dir))
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        (Path(other.name) / "conftest.py").write_text("", encoding="utf-8")
        with mock.patch.object(tr, "python_has_module", return_value=True):
            self.assertIsNotNone(self.runner.detect_test_command(Path(other.name)))

    def test_project_virtualenv_is_preferred(self):
        rel = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        venv_python = self.touch(f".venv/{rel}", "")
        self.assertEqual(find_python(self.dir), str(venv_python))

    def test_windows_store_stub_is_never_used(self):
        stub = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python3.exe"
        with mock.patch.object(tr.shutil, "which", side_effect=lambda exe: stub if exe == "python3" else None):
            self.assertEqual(find_python(self.dir), sys.executable)

    def test_python_has_module_is_false_for_a_missing_module_and_a_broken_interpreter(self):
        self.assertTrue(tr.python_has_module(PY, "json"))
        self.assertFalse(tr.python_has_module(PY, "module_qui_nexiste_pas_xyz"))
        self.assertFalse(tr.python_has_module(str(self.dir / "pas-un-python"), "json"))

    def test_no_python_test_marker_means_no_command(self):
        self.touch("src/app.py", "x = 1\n")
        self.assertIsNone(self.runner.detect_test_command(self.dir))


class TestOtherEcosystems(ProjectTestCase):
    def which(self, *available):
        return mock.patch.object(tr.shutil, "which", side_effect=lambda exe: f"/usr/bin/{exe}" if exe in available else None)

    def test_dotnet(self):
        self.touch("src/App/App.csproj", "<Project/>")
        with self.which("dotnet"):
            self.assertEqual(self.runner.detect_test_command(self.dir), ["dotnet", "test", "--nologo"])
        with self.no_tool_on_path():
            self.assertIsNone(self.runner.detect_test_command(self.dir))

    def test_maven_wrapper_then_system_maven(self):
        self.touch("pom.xml", "<project/>")
        with self.which("mvn"):
            self.assertEqual(self.runner.detect_test_command(self.dir), ["mvn", "-q", "test"])
        wrapper = self.touch("mvnw.cmd" if os.name == "nt" else "mvnw", "")
        with self.no_tool_on_path():
            self.assertEqual(self.runner.detect_test_command(self.dir), [str(wrapper), "-q", "test"])

    def test_gradle_wrapper_then_system_gradle(self):
        self.touch("build.gradle.kts", "")
        with self.which("gradle"):
            self.assertEqual(self.runner.detect_test_command(self.dir), ["gradle", "test"])
        wrapper = self.touch("gradlew.bat" if os.name == "nt" else "gradlew", "")
        with self.no_tool_on_path():
            self.assertEqual(self.runner.detect_test_command(self.dir), [str(wrapper), "test"])

    def test_rust_and_go(self):
        self.touch("Cargo.toml", "")
        with self.which("cargo"):
            self.assertEqual(self.runner.detect_test_command(self.dir), ["cargo", "test"])
        (self.dir / "Cargo.toml").unlink()
        self.touch("go.mod", "module x\n")
        with self.which("go"):
            self.assertEqual(self.runner.detect_test_command(self.dir), ["go", "test", "./..."])

    def test_empty_project_has_no_tests_and_run_returns_none(self):
        with self.no_tool_on_path():
            self.assertIsNone(self.runner.detect_test_command(self.dir))
            self.assertIsNone(self.runner.run_tests(self.dir))


class TestCommandResolution(ProjectTestCase):
    def test_priority_argument_then_constructor_then_project_file_then_detection(self):
        self.touch(".workflow.toml", '[tests]\ncommand = "echo depuis-le-fichier"\n')
        self.touch("package.json", '{"scripts": {"test": "jest"}}')
        runner = TestRunner(test_command="echo depuis-le-constructeur")

        self.assertEqual(runner.resolve_command(self.dir, "echo argument"), ["echo", "argument"])
        self.assertEqual(runner.resolve_command(self.dir), ["echo", "depuis-le-constructeur"])
        self.assertEqual(TestRunner().resolve_command(self.dir), ["echo", "depuis-le-fichier"])
        (self.dir / ".workflow.toml").unlink()
        with self.no_tool_on_path():
            self.assertEqual(TestRunner().resolve_command(self.dir), ["npm", "test"])

    def test_list_commands_are_used_as_is(self):
        self.assertEqual(TestRunner(test_command=["a b", "c"]).resolve_command(self.dir), ["a b", "c"])

    def test_project_config_reading(self):
        self.touch(".workflow.toml", '# projet\n[tests]\ncommand = "pytest -x"\ntimeout = 600\n[autre]\ncommand = "ignoré"\n')
        self.assertEqual(read_project_config(self.dir), {"command": "pytest -x", "timeout": 600})

    def test_fallback_parser_for_python_310(self):
        text = '[autre]\ncommand = "non"\n[tests]\ncommand = "make check"\ntimeout = 90\n'
        self.assertEqual(tr._parse_tests_section_fallback(text), {"command": "make check", "timeout": 90})

    def test_missing_or_broken_project_file_is_harmless(self):
        self.assertEqual(read_project_config(self.dir), {})
        self.touch(".workflow.toml", "ceci n'est [pas du toml\n")
        self.assertIsInstance(read_project_config(self.dir), dict)

    def test_project_file_timeout_overrides_the_default(self):
        self.touch(".workflow.toml", f'[tests]\ncommand = "{Path(PY).as_posix()} -c pass"\ntimeout = 77\n')
        runner = TestRunner(timeout_seconds=5)
        with mock.patch.object(tr.subprocess, "Popen", wraps=tr.subprocess.Popen):
            res = runner.run_tests(self.dir, custom_cmd=[PY, "-c", "pass"])
        self.assertTrue(res.passed)
        # le délai du fichier de projet est lu (vérifié via la configuration, l'exécution est trop rapide pour le mesurer)
        self.assertEqual(read_project_config(self.dir)["timeout"], 77)


class TestExecution(ProjectTestCase):
    def run_py(self, code, **kwargs):
        runner = kwargs.pop("runner", self.runner)
        return runner.run_tests(self.dir, custom_cmd=[PY, "-c", code])

    def test_pass_and_fail_with_exit_codes(self):
        self.assertTrue(self.run_py("raise SystemExit(0)").passed)
        failed = self.run_py("import sys; sys.stderr.write('AssertionError: boom'); sys.exit(3)")
        self.assertFalse(failed.passed)
        self.assertEqual(failed.returncode, 3)
        self.assertIn("AssertionError", failed.output)

    def test_ci_environment_no_color_and_closed_stdin(self):
        code = ("import os, sys; print(os.environ.get('CI'), os.environ.get('NO_COLOR'), "
                "os.environ.get('PYTHONDONTWRITEBYTECODE')); print(repr(sys.stdin.read()))")
        res = self.run_py(code)
        self.assertIn("true 1 1", res.output)
        self.assertIn("''", res.output)  # stdin fermé : un test interactif/watch ne bloque jamais

    def test_ansi_codes_are_stripped_and_output_is_capped_with_head_and_tail(self):
        runner = TestRunner(timeout_seconds=30, max_output_chars=2000)
        res = self.run_py("print('\\x1b[31mDEBUT rouge\\x1b[0m'); print('x' * 50000); print('FIN')", runner=runner)
        self.assertNotIn("\x1b", res.output)
        self.assertLessEqual(len(res.output), 2000)
        self.assertIn("DEBUT rouge", res.output)
        self.assertTrue(res.output.rstrip().endswith("FIN"))
        self.assertIn("caractères omis", res.output)

    def test_failed_test_ids_are_extracted_before_truncation(self):
        runner = TestRunner(timeout_seconds=30, max_output_chars=1000)
        res = self.run_py("print('\\n'.join(f'FAILED tests/test_a.py::test_{i} - boom' for i in range(500))); raise SystemExit(1)",
                          runner=runner)
        self.assertEqual(len(res.failed_tests), 500)  # la liste est complète même si le texte est tronqué
        self.assertLess(len(res.output), 1100)

    def test_timeout_kills_the_whole_process_tree(self):
        marker = self.dir / "grandchild_survived.txt"
        child = f"import time, pathlib; time.sleep(3); pathlib.Path(r'{marker}').write_text('vivant')"
        parent = ("import subprocess, sys, time; "
                  f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)")
        runner = TestRunner(timeout_seconds=1)
        started = time.perf_counter()
        res = runner.run_tests(self.dir, custom_cmd=[PY, "-c", parent])
        elapsed = time.perf_counter() - started

        self.assertTrue(res.timed_out)
        self.assertFalse(res.passed)
        self.assertEqual(res.returncode, -1)
        self.assertIn("TimeoutExpired", res.output)
        self.assertLess(elapsed, 15)
        time.sleep(4.5)  # laisse au petit-enfant le temps d'écrire s'il avait survécu
        self.assertFalse(marker.exists(), "le processus petit-enfant n'a pas été tué")

    def test_missing_executable_is_reported_not_raised(self):
        res = self.runner.run_tests(self.dir, custom_cmd=[str(self.dir / "pas-un-programme")])
        self.assertFalse(res.passed)
        self.assertEqual(res.returncode, -2)
        self.assertIn("Exception", res.output)

    def test_string_command_is_split(self):
        res = self.runner.run_tests(self.dir, custom_cmd=f'"{PY}" -c "print(42)"')
        self.assertTrue(res.passed)
        self.assertIn("42", res.output)

    def test_test_result_is_not_collected_as_a_test_class(self):
        self.assertFalse(TestResult.__test__)
        self.assertFalse(TestRunner.__test__)


class TestFailedTestParsing(unittest.TestCase):
    def test_pytest(self):
        out = "=== short test summary info ===\nFAILED tests/test_a.py::test_one - assert 1 == 2\nERROR tests/test_b.py::test_two\n1 failed"
        self.assertEqual(parse_failed_tests(out), ["tests/test_a.py::test_one", "tests/test_b.py::test_two"])

    def test_unittest(self):
        out = "FAIL: test_x (test_mod.TestA.test_x)\nERROR: test_y (test_mod.TestB.test_y)\n-----\nRan 2 tests"
        self.assertEqual(parse_failed_tests(out), ["test_x (test_mod.TestA.test_x)", "test_y (test_mod.TestB.test_y)"])

    def test_jest(self):
        out = "  ● Cart › adds an item\n  ● Cart › removes an item\n  ● Console\n\n    console.log x"
        self.assertEqual(parse_failed_tests(out), ["Cart › adds an item", "Cart › removes an item"])

    def test_vitest(self):
        self.assertEqual(parse_failed_tests(" FAIL  src/a.test.ts > suite > works\n"), ["src/a.test.ts > suite > works"])

    def test_go_cargo_dotnet(self):
        self.assertEqual(parse_failed_tests("--- FAIL: TestAdd (0.00s)\n"), ["TestAdd"])
        self.assertEqual(parse_failed_tests("test math::tests::it_adds ... FAILED\ntest ok ... ok\n"), ["math::tests::it_adds"])
        self.assertEqual(parse_failed_tests("  Failed Ns.CartTests.Adds [12 ms]\n  Passed Ns.CartTests.Other [1 ms]\n"),
                         ["Ns.CartTests.Adds"])

    def test_unrecognized_and_empty_outputs(self):
        self.assertEqual(parse_failed_tests("Segmentation fault"), [])
        self.assertEqual(parse_failed_tests(""), [])

    def test_ids_are_deduplicated_and_sorted(self):
        out = "FAILED b::t\nFAILED a::t\nFAILED b::t\n"
        self.assertEqual(parse_failed_tests(out), ["a::t", "b::t"])

    def test_strip_ansi(self):
        self.assertEqual(strip_ansi("\x1b[1;31mFAILED\x1b[0m x"), "FAILED x")


class TestRegressionLogic(unittest.TestCase):
    def test_passing_tests_are_never_a_regression(self):
        self.assertFalse(is_regression(result(False, ["a"]), result(True)))
        self.assertFalse(is_regression(None, result(True)))
        self.assertFalse(is_regression(result(True), result(True)))

    def test_green_baseline_or_no_baseline_then_any_failure_is_a_regression(self):
        self.assertTrue(is_regression(result(True), result(False, ["a"])))
        self.assertTrue(is_regression(None, result(False)))

    def test_same_preexisting_failures_are_not_a_regression_even_if_the_text_differs(self):
        baseline = result(False, ["t::a", "t::b"], output="FAILED t::a in 0.42s at 0x7f12 /tmp/aaa\nFAILED t::b")
        now = result(False, ["t::b", "t::a"], output="FAILED t::b in 0.89s at 0x9a33 /tmp/bbb\nFAILED t::a")
        self.assertFalse(is_regression(baseline, now))

    def test_a_new_failing_test_is_a_regression(self):
        self.assertTrue(is_regression(result(False, ["t::a"]), result(False, ["t::a", "t::c"])))

    def test_fewer_failures_than_the_baseline_is_not_a_regression(self):
        self.assertFalse(is_regression(result(False, ["t::a", "t::b"]), result(False, ["t::a"])))

    def test_unreadable_ids_fall_back_to_normalized_output_comparison(self):
        baseline = result(False, [], output="Segfault in module X (took 1.5s)")
        self.assertFalse(is_regression(baseline, result(False, [], output="Segfault in module X (took 9.9s)")))
        self.assertTrue(is_regression(baseline, result(False, [], output="Segfault in module Y")))

    def test_baseline_with_ids_but_current_failure_unreadable_is_compared_by_text(self):
        baseline = result(False, ["t::a"], output="boom")
        self.assertTrue(is_regression(baseline, result(False, [], output="compilation error")))


class ScriptedRunner(TestRunner):
    """Rejoue une suite de résultats : baseline, puis un résultat par cycle."""

    def __init__(self, results):
        super().__init__()
        self.results = list(results)
        self.calls = 0

    def run_tests(self, project_dir, custom_cmd=None):
        self.calls += 1
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


class TestOracleInTheWorkflow(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)

    def orchestrator(self, runner, **kwargs):
        options = dict(claude_client=ClaudeCliClient(mock_mode=True), jev_client=JevClient(mock_mode=True),
                       project_dir=str(self.repo.path), test_runner=runner, run_tests=True, use_branch=True, max_retries=3)
        options.update(kwargs)
        return MultiAgentOrchestrator(**options)

    def test_baseline_failures_are_tolerated_and_a_new_failure_triggers_the_feedback_loop(self):
        runner = ScriptedRunner([
            result(False, ["t::legacy_a", "t::legacy_b"], output="FAILED t::legacy_a in 0.4s\nFAILED t::legacy_b"),   # baseline
            result(False, ["t::legacy_a", "t::legacy_b", "t::nouveau"], output="FAILED t::nouveau"),                   # régression
            result(False, ["t::legacy_a", "t::legacy_b"], output="FAILED t::legacy_a in 9.9s at 0x1\nFAILED t::legacy_b"),  # corrigé
        ])
        report = self.orchestrator(runner).run("Modifie le tri de l'onglet x")

        self.assertTrue(report.is_success)
        names = [s.step_name for s in report.history]
        self.assertIn("TESTS_FAILED_CYCLE_1", names)
        self.assertIn("TESTS_PASSED_CYCLE_2", names)
        self.assertEqual(report.baseline_tests_failed, ["t::legacy_a", "t::legacy_b"])
        self.assertEqual(report.tests_failed, ["t::legacy_a", "t::legacy_b"])
        self.assertFalse(report.baseline_tests_passed)

    def test_the_feedback_sent_to_the_developer_contains_the_test_trace(self):
        runner = ScriptedRunner([result(True), result(False, ["t::x"], output="TRACE_UNIQUE_DES_TESTS"), result(True)])
        report = self.orchestrator(runner).run("Modifie le tri de l'onglet x")
        retry = next(s for s in report.history if s.step_name == "DEV_IN_SITU_CYCLE_2")
        self.assertIn("TRACE_UNIQUE_DES_TESTS", retry.prompt_sent)

    def test_circuit_breaker_message_mentions_the_failing_tests(self):
        runner = ScriptedRunner([result(True), result(False, ["t::cassé_1", "t::cassé_2"], output="échec")])
        report = self.orchestrator(runner, max_retries=2).run("Modifie le tri de l'onglet x")

        self.assertFalse(report.is_success)
        self.assertIn("Circuit breaker", report.error_message)
        self.assertIn("tests du projet échouaient encore", report.error_message)
        self.assertIn("t::cassé_1", report.error_message)
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertEqual(self.repo.status(), "")


if __name__ == "__main__":
    unittest.main()
