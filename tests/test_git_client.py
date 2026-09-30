"""
Tests du GitClient sur de vrais dépôts temporaires : rollback, stash, branches, commit, parsing.
"""

import os
import stat
import unittest
from unittest import mock

from gitrepo import make_repo

from workflow_claude.clients.git_client import GitClient, GitClientError


class GitClientTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)
        self.git = GitClient(str(self.repo.path))


class TestPreconditions(GitClientTestCase):
    def test_empty_repository_has_no_commits(self):
        empty = make_repo(commit=False)
        self.addCleanup(empty.cleanup)
        client = GitClient(str(empty.path))
        self.assertTrue(client.is_git_repository())
        self.assertFalse(client.has_commits())
        self.assertTrue(self.git.has_commits())

    def test_identity_detection(self):
        no_identity = make_repo(identity=False, commit=False)
        self.addCleanup(no_identity.cleanup)
        isolated_env = {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        with mock.patch.dict(os.environ, isolated_env):
            for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
                os.environ.pop(var, None)
            self.assertFalse(GitClient(str(no_identity.path)).has_identity())
        self.assertTrue(self.git.has_identity())

    def test_root_is_repository_toplevel_for_a_subdirectory(self):
        client = GitClient(str(self.repo.path / "src" / "components"))
        self.assertEqual(client.root, self.repo.path)
        self.assertTrue(client.is_git_repository())


class TestRollback(GitClientTestCase):
    def test_rollback_restores_modified_and_removes_new_files(self):
        original = self.repo.read("src/components/TabX.tsx")
        self.repo.write("src/components/TabX.tsx", "changed\n")
        self.repo.write("src/components/Brand.tsx", "export const Brand = 1;\n")
        self.git.get_diff()  # git add -N : le nouveau fichier devient « intent-to-add »

        self.git.rollback()

        self.assertEqual(self.repo.read("src/components/TabX.tsx"), original)
        self.assertFalse((self.repo.path / "src" / "components" / "Brand.tsx").exists())
        self.assertEqual(self.repo.status(), "")

    def test_rollback_discards_staged_changes(self):
        self.repo.write("README.md", "# staged change\n")
        self.repo.git("add", "README.md")
        self.git.rollback()
        self.assertEqual(self.repo.read("README.md"), "# Test Project\n")
        self.assertEqual(self.repo.status(), "")


class TestDiffAndFiles(GitClientTestCase):
    def test_diff_includes_untracked_new_file(self):
        self.repo.write("src/New.tsx", "export const New = () => <b>new</b>;\n")
        diff = self.git.get_diff()
        self.assertIn("New.tsx", diff)
        self.assertIn("<b>new</b>", diff)

    def test_modified_files_handles_spaces_and_renames(self):
        self.repo.write("docs/notes with spaces.md", "hello\n")
        self.repo.git("mv", "README.md", "READ ME.md")
        files = self.git.get_modified_files()
        self.assertIn("docs/notes with spaces.md", files)
        self.assertIn("READ ME.md", files)
        self.assertNotIn("README.md", files)

    def test_untracked_files_and_selective_removal(self):
        self.repo.write("keep.txt", "k\n")
        self.repo.write("artifact/cache.bin", "x\n")
        untracked = self.git.untracked_files()
        self.assertIn("keep.txt", untracked)
        self.assertIn("artifact/cache.bin", untracked)

        self.git.remove_untracked(["artifact/cache.bin"])
        self.assertTrue((self.repo.path / "keep.txt").exists())
        self.assertFalse((self.repo.path / "artifact" / "cache.bin").exists())


class TestStash(GitClientTestCase):
    def test_push_and_pop_by_message_even_when_not_on_top(self):
        self.repo.write("wip.txt", "my work\n")
        self.assertTrue(self.git.stash_push("workflow-auto-stash-A"))

        # Un autre stash (celui de l'utilisateur, plus récent) se retrouve au sommet de la pile
        self.repo.write("other.txt", "unrelated\n")
        self.repo.git("stash", "push", "-u", "-m", "user stash")

        self.assertTrue(self.git.stash_pop("workflow-auto-stash-A"))
        self.assertTrue((self.repo.path / "wip.txt").exists())
        self.assertFalse((self.repo.path / "other.txt").exists())  # le stash de l'utilisateur est intact
        self.assertEqual(len(self.repo.stashes()), 1)
        self.assertIn("user stash", self.repo.stashes()[0])

    def test_push_on_clean_tree_creates_nothing(self):
        self.assertFalse(self.git.stash_push("nothing"))
        self.assertEqual(self.repo.stashes(), [])

    def test_pop_unknown_message_returns_false(self):
        self.assertFalse(self.git.stash_pop("does-not-exist"))

    def test_pop_conflict_keeps_the_stash(self):
        self.repo.write("README.md", "# my wip\n")
        self.assertTrue(self.git.stash_push("workflow-auto-stash-B"))
        self.repo.write("README.md", "# concurrent change\n")
        self.repo.git("commit", "-am", "concurrent")

        self.assertFalse(self.git.stash_pop("workflow-auto-stash-B"))
        self.assertEqual(len(self.repo.stashes()), 1)  # conservé par git


class TestBranches(GitClientTestCase):
    def test_unique_branch_name_adds_suffix(self):
        self.git.create_and_checkout_branch("workflow/ai-1")
        self.git.checkout_branch("main")
        self.assertEqual(self.git.unique_branch_name("workflow/ai-1"), "workflow/ai-1-2")
        self.assertEqual(self.git.unique_branch_name("workflow/ai-9"), "workflow/ai-9")

    def test_detached_head_reference(self):
        sha = self.repo.git("rev-parse", "HEAD")
        self.repo.git("checkout", "--detach", sha)
        branch, head = self.git.get_head_ref()
        self.assertIsNone(branch)
        self.assertEqual(head, sha)
        self.assertEqual(self.git.get_current_branch(), "HEAD")

    def test_merge_conflict_is_aborted_and_tree_is_clean(self):
        self.git.create_and_checkout_branch("feature")
        self.repo.write("README.md", "# feature side\n")
        self.repo.git("commit", "-am", "feature change")
        self.git.checkout_branch("main")
        self.repo.write("README.md", "# main side\n")
        self.repo.git("commit", "-am", "main change")

        self.assertFalse(self.git.merge_branch("feature"))
        self.assertEqual(self.repo.status(), "")
        self.assertEqual(self.repo.read("README.md"), "# main side\n")

    def test_count_commits_ahead(self):
        base = self.repo.git("rev-parse", "HEAD")
        self.git.create_and_checkout_branch("feature")
        self.repo.write("a.txt", "a\n")
        self.git.commit("feat: a")
        self.assertEqual(self.git.count_commits_ahead(base, "feature"), 1)


class TestCommit(GitClientTestCase):
    def test_commit_uses_multiline_message_from_stdin(self):
        self.repo.write("a.txt", "a\n")
        self.git.commit("feat(core): add a\n\nBody line 1\nBody line 2")
        message = self.repo.git("log", "-1", "--format=%B")
        self.assertTrue(message.startswith("feat(core): add a"))
        self.assertIn("Body line 2", message)

    def test_commit_failure_raises_instead_of_returning_none(self):
        hook = self.repo.path / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho 'refused by hook' >&2\nexit 1\n", encoding="utf-8", newline="\n")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
        self.repo.write("a.txt", "a\n")
        with self.assertRaises(GitClientError):
            self.git.commit("feat: a")


if __name__ == "__main__":
    unittest.main()
