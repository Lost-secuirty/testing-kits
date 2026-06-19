# Failure example: RAG fabricated citation

This is an explanatory failure example. It is not current command output unless a PR or CI log explicitly cites a reproduced run.

## Contract

A RAG evaluation harness must reject answers that cite sources not present in the retrieved evidence set or that attribute unsupported claims to retrieved documents.

## Bad implementation

The evaluator checks that the answer contains citation-looking text, but does not verify that the cited source exists in the retrieval set or supports the claim.

## Expected proof behavior

- Supported answers with valid evidence remain clean.
- Fabricated or unsupported citations are caught.
- The planted-bad evaluator fails because it accepts citation format instead of evidence grounding.

## Why this matters

A RAG system can look reliable because every answer has citations. Citation formatting is not grounding. The proof must check whether the cited evidence exists and supports the claim.

## Reproduction command shape

```bash
python -m unittest tests.ai.test_rag_eval_proof
```

Use the actual test module name in the repo if it differs.

## Porting notes

When porting this failure class, define:

- the retrieved document IDs;
- allowed evidence spans or facts;
- unsupported claims;
- citation format expectations;
- whether partial support is acceptable;
- how conflicting evidence is handled.

Do not use the model's own explanation as the oracle. Use frozen evidence or an independent evaluator contract.
