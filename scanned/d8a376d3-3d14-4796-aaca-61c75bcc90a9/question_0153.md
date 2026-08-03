# Q0153: Extract lower-trust restore poisons protected-job inputs

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `Extract` restore lower-trust state that later protected-ref jobs consume because the restore boundary is bound too loosely to ref or protection status?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_extractor.go: Extract
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, cross-ref artifact or cache state, protected and unprotected refs
- Exploit idea: reuse lower-trust restored content across a protected boundary
- Invariant to test: restore state must stay bound to the correct ref and protection boundary
- Expected Immunefi impact: protected-job escalation through restored state poisoning
- Fast validation: seed restore state from an unprotected ref and verify a protected ref never consumes it
