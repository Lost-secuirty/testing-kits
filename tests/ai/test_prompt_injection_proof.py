import unittest

from harnesses.ai import prompt_injection_test_harness as harness


class TestPromptInjectionProof(unittest.TestCase):
    def test_oracle_matches_frozen_prompt_injection_cases(self):
        for case in harness.PROMPT_INJECTION_AUDIT_CORPUS:
            self.assertEqual(
                harness.oracle_prompt_injection_audit(case),
                case.expected_verdict,
                case.name,
            )

    def test_prove_keeps_oracle_clean_and_catches_mutants(self):
        self.assertFalse(harness.prove(harness.oracle_prompt_injection_audit))
        self.assertEqual(harness.TEETH.corpus_size, len(harness.PROMPT_INJECTION_AUDIT_CORPUS))
        for mutant in harness.TEETH.mutants:
            self.assertTrue(harness.prove(mutant.impl), mutant.name)

    def test_planted_prompt_injection_defects_have_traps(self):
        cases = {case.name: case for case in harness.PROMPT_INJECTION_AUDIT_CORPUS}
        jailbreak_case = cases["jailbreak_roleplay_hacker"]
        benign_ignore_case = cases["benign_ignore_word_overlap"]
        leak_case = cases["system_prompt_leak_request"]
        indirect_case = cases["indirect_system_tag_in_tool_output"]

        self.assertNotEqual(
            harness.direct_only_prompt_guard(jailbreak_case),
            jailbreak_case.expected_verdict,
        )
        self.assertNotEqual(
            harness.overbroad_keyword_prompt_guard(benign_ignore_case),
            benign_ignore_case.expected_verdict,
        )
        self.assertNotEqual(
            harness.leak_blind_prompt_guard(leak_case),
            leak_case.expected_verdict,
        )
        self.assertNotEqual(
            harness.delimiter_blind_prompt_guard(indirect_case),
            indirect_case.expected_verdict,
        )


if __name__ == "__main__":
    unittest.main()
