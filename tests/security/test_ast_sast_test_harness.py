"""test_ast_sast_test_harness.py — unittest suite."""

import unittest

from harnesses.security.ast_sast_test_harness import (
    ASTSAST,
    list_scenarios,
    run_all_scenarios,
)


class TestASTSAST(unittest.TestCase):
    def setUp(self):
        self.s = ASTSAST()

    def _cwes(self, src):
        return {f.cwe for f in self.s.scan(src)}

    def test_eval_flagged(self):
        self.assertIn("CWE-95", self._cwes("def f(e):\n    return eval(e)\n"))

    def test_os_system_flagged(self):
        self.assertIn("CWE-78", self._cwes("import os\nos.system(x)\n"))

    def test_shell_true_flagged(self):
        self.assertIn("CWE-78", self._cwes("import subprocess\nsubprocess.run(c, shell=True)\n"))

    def test_sql_fstring_flagged(self):
        self.assertIn("CWE-89", self._cwes('cur.execute(f"SELECT {x}")\n'))

    def test_sql_param_clean(self):
        self.assertNotIn("CWE-89", self._cwes('cur.execute("SELECT ?", (x,))\n'))

    def test_pickle_flagged(self):
        self.assertIn("CWE-502", self._cwes("import pickle\npickle.loads(b)\n"))

    def test_md5_flagged(self):
        self.assertIn("CWE-327", self._cwes("import hashlib\nhashlib.md5(b)\n"))

    def test_random_flagged(self):
        self.assertIn("CWE-330", self._cwes("import random\nrandom.randint(0, 9)\n"))

    def test_verify_false_flagged(self):
        self.assertIn("CWE-295", self._cwes("requests.get(u, verify=False)\n"))

    def test_hardcoded_secret_flagged(self):
        self.assertIn("CWE-798", self._cwes('API_KEY = "AKIAIOSFODNN7EXAMPLE"\n'))

    def test_env_secret_clean(self):
        self.assertNotIn("CWE-798", self._cwes('API_KEY = os.environ["API_KEY"]\n'))

    def test_mktemp_flagged(self):
        self.assertIn("CWE-377", self._cwes("import tempfile\ntempfile.mktemp()\n"))

    def test_assert_auth_flagged(self):
        self.assertIn("CWE-617", self._cwes("def g(u):\n    assert u.is_admin\n"))

    # --- added rules ---
    def test_debug_run_flagged(self):
        self.assertIn("CWE-489", self._cwes("app.run(debug=True)\n"))

    def test_no_timeout_flagged(self):
        self.assertIn("CWE-400", self._cwes("import requests\nrequests.get(u)\n"))

    def test_timeout_present_clean(self):
        self.assertNotIn("CWE-400", self._cwes("import requests\nrequests.get(u, timeout=5)\n"))

    def test_archive_extractall_flagged(self):
        self.assertIn("CWE-22", self._cwes("import tarfile\ntarfile.open(p).extractall('/tmp')\n"))

    def test_jinja_autoescape_flagged(self):
        self.assertIn("CWE-79", self._cwes("import jinja2\nenv = jinja2.Environment()\n"))

    def test_jinja_autoescape_on_clean(self):
        self.assertNotIn("CWE-79", self._cwes("import jinja2\nenv = jinja2.Environment(autoescape=True)\n"))

    def test_syntax_error_no_crash(self):
        self.assertEqual(self.s.scan("def ( bad"), [])

    def test_findings_have_line_numbers(self):
        findings = self.s.scan("\n\nimport os\nos.system(x)\n")
        self.assertTrue(findings)
        self.assertEqual(findings[0].line, 4)


class TestSelfTest(unittest.TestCase):
    def test_all_scenarios_pass(self):
        results = run_all_scenarios(verbose=False)
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [], "Failed: " + ", ".join(r.name for r in failed))

    def test_scenario_count(self):
        self.assertGreaterEqual(len(list_scenarios()), 17)


if __name__ == "__main__":
    unittest.main()
