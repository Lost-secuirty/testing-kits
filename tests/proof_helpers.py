def assert_oracle_matches(testcase, corpus, oracle):
    for case in corpus:
        testcase.assertEqual(oracle(case), case.expected_events, case.name)


def assert_teeth_contract(testcase, harness, corpus):
    testcase.assertFalse(harness.prove(harness.TEETH.oracle))
    testcase.assertEqual(harness.TEETH.corpus_size, len(corpus))
    for mutant in harness.TEETH.mutants:
        testcase.assertTrue(harness.prove(mutant.impl), mutant.name)


def assert_mutants_differ(testcase, corpus, mutant_cases):
    cases = {case.name: case for case in corpus}
    for case_name, mutant in mutant_cases:
        case = cases[case_name]
        testcase.assertNotEqual(mutant(case), case.expected_events, case_name)
