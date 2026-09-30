import unittest

from workflow_claude.commit_message import extract_commit_message


class TestExtractCommitMessage(unittest.TestCase):
    def test_extracts_subject_and_body_from_fenced_block(self):
        doc = (
            "## DOCUMENTATION\nTexte.\n\n## PROPOSITION DE COMMIT GIT\n"
            "```git\nfeat(core): add robust pipeline\n\n- Add validation\n- Handle errors\n```"
        )
        msg = extract_commit_message(doc, "prompt")
        self.assertTrue(msg.startswith("feat(core): add robust pipeline"))
        self.assertIn("- Add validation", msg)
        self.assertNotIn("```", msg)

    def test_plain_line_without_fence(self):
        msg = extract_commit_message("Voici :\n**fix(api): handle None input**\nDétails", "prompt")
        self.assertEqual(msg, "fix(api): handle None input")

    def test_feature_request_is_not_a_conventional_type(self):
        msg = extract_commit_message("feature request: add tabs\nfixing things: later", "Ajoute des onglets")
        self.assertEqual(msg, "chore: Ajoute des onglets")

    def test_fallback_when_nothing_found_or_empty(self):
        self.assertEqual(extract_commit_message("", "Tri par date"), "chore: Tri par date")
        self.assertTrue(extract_commit_message("rien d'exploitable", "x").startswith("chore: "))

    def test_breaking_change_marker_and_scope_with_slash(self):
        msg = extract_commit_message("refactor(api/v2)!: drop legacy route", "p")
        self.assertEqual(msg, "refactor(api/v2)!: drop legacy route")

    def test_subject_is_truncated(self):
        msg = extract_commit_message("feat: " + "x" * 300, "p")
        self.assertLessEqual(len(msg.splitlines()[0]), 100)


if __name__ == "__main__":
    unittest.main()
