# Failure example: JWT alg=none acceptance

This is an explanatory failure example. It is not current command output unless a PR or CI log explicitly cites a reproduced run.

## Contract

A JWT verifier must reject unsigned `alg=none` tokens unless the target contract explicitly allows unsecured JWTs for a narrow non-production use case.

## Bad implementation

The verifier accepts a token when the payload parses and skips signature verification.

## Expected proof behavior

- The correct oracle remains clean.
- The `alg=none` planted-bad case is caught.
- The proof fails loudly if the mutant accepts the unsigned token.

## Why this matters

A green test suite that does not reject `alg=none` can falsely certify an authentication bypass.

## Reproduction command shape

```bash
python -m unittest tests.security.test_jwt_proof
```

Use the actual test module name in the repo if it differs.

## Porting notes

When porting this failure class, state whether the target contract requires:

- algorithm allow-listing;
- signature verification;
- issuer validation;
- audience validation;
- expiration validation;
- required claims;
- key rotation or JWK handling.

Do not claim the portable harness proves target provider configuration unless target-specific integration tests exist.
