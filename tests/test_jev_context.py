import unittest

from workflow_claude.jev_context import (
    build_validation_context,
    mask_secrets,
    parse_verdict,
    prepare_context,
    truncate_middle,
)


class TestMaskSecrets(unittest.TestCase):
    def assertMasked(self, text, secret, kind=None):
        masked = mask_secrets(text)
        self.assertNotIn(secret, masked, text)
        self.assertIn("[REDACTED", masked)
        if kind:
            self.assertIn(f"[REDACTED:{kind}]", masked)

    def test_known_token_formats(self):
        self.assertMasked("k = 'sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "anthropic-key")
        self.assertMasked("use sk-proj-abcdefghijklmnopqrstuvwxyz123456 now", "abcdefghijklmnopqrstuvwxyz123456", "openai-style-key")
        self.assertMasked("GH=ghp_abcdefghijklmnopqrstuvwxyz0123456789", "abcdefghijklmnopqrstuvwxyz0123456789", "github-token")
        self.assertMasked("id AKIAIOSFODNN7EXAMPLE end", "AKIAIOSFODNN7EXAMPLE", "aws-access-key")
        self.assertMasked("g: AIzaSyA1234567890abcdefghijklmnopqrstuv", "AIzaSyA1234567890abcdefghijklmnopqrstuv", "google-api-key")
        self.assertMasked("s = xoxb-1234567890-abcdefghij", "xoxb-1234567890-abcdefghij", "slack-token")
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz"
        self.assertMasked(f"token {jwt}", jwt, "jwt")

    def test_private_key_block_is_fully_removed(self):
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234\nabcdef\n-----END RSA PRIVATE KEY-----"
        masked = mask_secrets(f"avant\n{block}\naprès")
        self.assertNotIn("MIIEowIBAAKCAQEA1234", masked)
        self.assertIn("avant", masked)
        self.assertIn("après", masked)

    def test_bearer_and_url_credentials_keep_their_structure(self):
        masked = mask_secrets("Authorization: Bearer abcdefghijklmnop1234567890")
        self.assertIn("Bearer [REDACTED:bearer-token]", masked)
        masked = mask_secrets("DB=postgres://admin:s3cretPass@db.example.com:5432/app")
        self.assertIn("postgres://admin:[REDACTED:url-credentials]@db.example.com", masked)
        self.assertNotIn("s3cretPass", masked)

    def test_credential_assignments_in_several_syntaxes(self):
        for line, secret in (
            ("password = 'hunter2hunter2'", "hunter2hunter2"),
            ('PASSWORD: "hunter2hunter2"', "hunter2hunter2"),
            ('{"api_key": "abc123def456"}', "abc123def456"),
            ("const apiKey = 'abc123def456';", "abc123def456"),
            ("export SECRET_TOKEN=abc123def456", "abc123def456"),
            ("db.passwd=abc123def456", "abc123def456"),
        ):
            with self.subTest(line=line):
                self.assertMasked(line, secret)

    def test_name_is_kept_and_only_the_value_is_masked(self):
        self.assertEqual(mask_secrets("password = 'hunter2hunter2'"), "password = '[REDACTED:credential-assignment]'")

    def test_normal_code_is_left_alone(self):
        code = "def get_token_count():\n    token_count = 5\n    key = value\n    return token_count + len(keys)\n"
        self.assertEqual(mask_secrets(code), code)

    def test_no_double_masking_of_placeholders(self):
        masked = mask_secrets('API_KEY = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"')
        self.assertEqual(masked, 'API_KEY = "[REDACTED:anthropic-key]"')

    def test_configured_secret_is_masked_wherever_it_appears(self):
        masked = mask_secrets("la clé vaut tsk-LOCAL-KEY-1234567890 ici", extra_secrets=["tsk-LOCAL-KEY-1234567890"])
        self.assertNotIn("tsk-LOCAL-KEY-1234567890", masked)
        self.assertIn("configured-secret", masked)

    def test_empty_and_none_safe(self):
        self.assertEqual(mask_secrets(""), "")
        self.assertEqual(mask_secrets(None), None)


class TestTruncate(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(truncate_middle("abc", 10), "abc")

    def test_long_text_keeps_head_and_tail_and_fits(self):
        text = "H" * 500 + "M" * 5000 + "T" * 500
        out = truncate_middle(text, 1000)
        self.assertLessEqual(len(out), 1000)
        self.assertTrue(out.startswith("HHH"))
        self.assertTrue(out.endswith("TTT"))
        self.assertIn("caractères omis", out)

    def test_zero_limit(self):
        self.assertEqual(truncate_middle("abc", 0), "")


class TestBuildValidationContext(unittest.TestCase):
    DIFF = "+def f():\n+    return 1\n+password = 'hunter2hunter2'\n"

    def test_full_mode_has_masked_diff_and_review(self):
        ctx = build_validation_context(self.DIFF, "Tout va bien", "Review Qualité", mode="full")
        self.assertTrue(ctx.startswith("Git Diff :\n"))
        self.assertIn("return 1", ctx)
        self.assertIn("Review Qualité :\nTout va bien", ctx)
        self.assertNotIn("hunter2hunter2", ctx)

    def test_review_only_mode_sends_no_code_at_all(self):
        ctx = build_validation_context(self.DIFF, "Tout va bien", "Review Qualité", mode="review-only")
        self.assertEqual(ctx, "Review Qualité :\nTout va bien")
        self.assertNotIn("return 1", ctx)

    def test_review_is_preserved_when_the_diff_is_huge(self):
        ctx = build_validation_context("x" * 500_000, "CONCLUSION IMPORTANTE", "Review", mode="full", max_chars=20_000)
        self.assertLessEqual(len(ctx), 20_000)
        self.assertIn("CONCLUSION IMPORTANTE", ctx)
        self.assertIn("caractères omis", ctx)

    def test_huge_review_is_capped_but_diff_still_fits(self):
        ctx = build_validation_context("+ligne utile\n", "r" * 500_000, "Review", mode="full", max_chars=20_000)
        self.assertLessEqual(len(ctx), 20_000)
        self.assertIn("+ligne utile", ctx)

    def test_custom_diff_label_and_extra_secret(self):
        ctx = build_validation_context("code avec SECRETVALUE1234", "ok", "Review Sécurité", diff_label="Code",
                                       extra_secrets=["SECRETVALUE1234"])
        self.assertTrue(ctx.startswith("Code :\n"))
        self.assertNotIn("SECRETVALUE1234", ctx)

    def test_prepare_context_masks_and_truncates(self):
        out = prepare_context("password = 'hunter2hunter2'\n" + "z" * 100_000, max_chars=10_000)
        self.assertLessEqual(len(out), 10_000)
        self.assertNotIn("hunter2hunter2", out)


class TestParseVerdict(unittest.TestCase):
    def test_pass_and_fail(self):
        self.assertIs(parse_verdict("analyse...\nVERDICT: PASS"), True)
        self.assertIs(parse_verdict("analyse...\nVERDICT: FAIL"), False)

    def test_markdown_decorations_and_case(self):
        self.assertIs(parse_verdict("**VERDICT: FAIL**"), False)
        self.assertIs(parse_verdict("`verdict: pass`"), True)
        self.assertIs(parse_verdict("> VERDICT : PASS"), True)

    def test_last_verdict_wins(self):
        self.assertIs(parse_verdict("VERDICT: PASS\nfinalement...\nVERDICT: FAIL"), False)

    def test_absent_or_embedded_in_a_sentence(self):
        self.assertIsNone(parse_verdict("pas de verdict ici"))
        self.assertIsNone(parse_verdict(""))
        self.assertIsNone(parse_verdict(None))
        self.assertIsNone(parse_verdict("Mon verdict est : PASS selon moi"))


if __name__ == "__main__":
    unittest.main()
