# Q0406: createBodyProvider duplicate member names poison a later restore

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `createBodyProvider` emit two members that collapse to the same final restore path so downstream extraction trusts attacker ordering?

## Target
- File/function: commands/helpers/artifacts_uploader.go: createBodyProvider
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, duplicate names and canonical aliases
- Exploit idea: emit colliding archive members that later restore into one trusted path
- Invariant to test: one final path must not be represented by multiple archive members
- Expected Immunefi impact: cross-job state tampering after restore
- Fast validation: build an archive with colliding names and verify collision rejection
