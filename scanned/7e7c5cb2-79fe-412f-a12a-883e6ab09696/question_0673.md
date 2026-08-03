# Q0673: downloadArtifactFile lower-trust restore poisons protected-job inputs

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadArtifactFile` restore lower-trust state that later protected-ref jobs consume because the restore boundary is bound too loosely to ref or protection status?

## Target
- File/function: network/gitlab.go: downloadArtifactFile
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, cross-ref artifact or cache state, protected and unprotected refs
- Exploit idea: reuse lower-trust restored content across a protected boundary
- Invariant to test: restore state must stay bound to the correct ref and protection boundary
- Expected Immunefi impact: protected-job escalation through restored state poisoning
- Fast validation: seed restore state from an unprotected ref and verify a protected ref never consumes it
