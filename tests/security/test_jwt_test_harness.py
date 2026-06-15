"""test_jwt_test_harness.py â€” unittest suite for jwt_test_harness (46)."""

import unittest

from harnesses.security.jwt_test_harness import (
    b64url_decode,
    b64url_encode,
    encode,
    forge_alg_none,
    forge_alg_swap,
    run_all_scenarios,
    tamper_payload,
    verify,
)

NOW = 1_700_000_000
KEY = "correct-horse-battery-staple"


def fresh(**over):
    payload = {"sub": "alice", "iat": NOW, "exp": NOW + 3600}
    payload.update(over)
    return encode(payload, KEY)


class TestBase64Url(unittest.TestCase):

    def test_roundtrip(self):
        for raw in [b"", b"a", b"ab", b"abc", b"\x00\xff\xfe", b"hello world"]:
            self.assertEqual(b64url_decode(b64url_encode(raw)), raw)

    def test_no_padding_in_output(self):
        self.assertNotIn("=", b64url_encode(b"abc"))


class TestHappyPath(unittest.TestCase):

    def test_valid(self):
        res = verify(fresh(), KEY, now=NOW, required_claims=("sub",))
        self.assertTrue(res.ok)
        self.assertEqual(res.payload["sub"], "alice")

    def test_base64url_roundtrip_payload(self):
        tok = encode({"sub": "u", "data": "a/b+c=", "exp": NOW + 10, "iat": NOW}, KEY)
        res = verify(tok, KEY, now=NOW)
        self.assertTrue(res.ok)
        self.assertEqual(res.payload["data"], "a/b+c=")


class TestAttacks(unittest.TestCase):

    def test_wrong_key(self):
        res = verify(fresh(), "wrong", now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "signature-mismatch")

    def test_alg_none(self):
        res = verify(forge_alg_none(fresh()), KEY, now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "alg-none-rejected")

    def test_alg_swap(self):
        res = verify(forge_alg_swap(fresh(), "HS384"), KEY, now=NOW)
        self.assertFalse(res.ok)
        self.assertTrue(res.reason.startswith("alg-not-allowed"))

    def test_tampered_payload(self):
        forged = tamper_payload(fresh(), lambda p: p.update({"sub": "admin"}))
        res = verify(forged, KEY, now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "signature-mismatch")

    def test_empty_signature(self):
        h, p, _ = fresh().split(".")
        res = verify(f"{h}.{p}.", KEY, now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "empty-signature")


class TestTimeClaims(unittest.TestCase):

    def test_expired(self):
        res = verify(fresh(exp=NOW - 1), KEY, now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "token-expired")

    def test_expiry_leeway(self):
        res = verify(fresh(exp=NOW - 5), KEY, now=NOW, leeway=10)
        self.assertTrue(res.ok)

    def test_nbf_future(self):
        res = verify(fresh(nbf=NOW + 100), KEY, now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "token-not-yet-valid")

    def test_iat_future(self):
        res = verify(fresh(iat=NOW + 100), KEY, now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "iat-in-future")


class TestStructuralAndClaims(unittest.TestCase):

    def test_missing_claim(self):
        tok = encode({"iat": NOW, "exp": NOW + 60}, KEY)
        res = verify(tok, KEY, now=NOW, required_claims=("sub",))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "missing-claim:sub")

    def test_malformed(self):
        res = verify("aaa.bbb", KEY, now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "malformed-token")

    def test_none_token(self):
        res = verify(None, KEY, now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, "token-not-string")


class TestSelfTest(unittest.TestCase):

    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(run_all_scenarios(verbose=False)), 14)


if __name__ == "__main__":
    unittest.main()
