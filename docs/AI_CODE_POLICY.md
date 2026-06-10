# AI-Assisted Code Review Policy

AI-assisted code is useful, but it is not trusted by default.

## Rule

Any AI-assisted change must be reviewed as untrusted code until it has:

1. a clear scope;
2. tests or proof checks;
3. reviewer notes for likely failure modes;
4. no unexplained generated dependencies;
5. no bypass of security, CI, or provenance rules.

## Required PR disclosure

PR