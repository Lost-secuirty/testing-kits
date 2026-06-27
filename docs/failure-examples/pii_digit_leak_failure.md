# Failure example: PII digit leak

This is an explanatory failure example. It is not current command output unless a PR or CI log explicitly cites a reproduced run.

## Contract

A PII redaction harness must detect sensitive digit patterns that the target contract says must not leave the system unredacted.

## Bad implementation

The redactor removes obvious labels such as `ssn` or `account`, but leaves the underlying digit sequence in logs, exports, or model-visible context.

## Expected proof behavior

- Safe text remains clean.
- Known sensitive digit patterns are caught.
- The planted-bad redactor fails because it leaks the protected value.

## Why this matters

A test can appear to check redaction while only checking that a redaction function was called. That is vacuous. The proof must inspect the resulting text for leaks.

## Reproduction command shape

```bash
python -m unittest tests.security.test_pii_redaction_proof
```

Use the actual test module name in the repo if it differs.

## Porting notes

When porting this failure class, define the protected data types explicitly:

- SSN-like identifiers;
- phone numbers;
- account numbers;
- card-like numbers;
- dates of birth;
- addresses;
- email addresses;
- domain-specific identifiers.

Also define over-redaction expectations. A useful redaction test should catch leaks without destroying safe text that the target contract permits.
