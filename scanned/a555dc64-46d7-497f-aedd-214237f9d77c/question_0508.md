# Q0508: downloadAllArtifacts partial restore leaves residue for a later stronger job

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadAllArtifacts` fail mid-restore while leaving lower-trust residue that a later protected or unrelated job consumes as if it were valid?

## Target
- File/function: shells/abstract.go: downloadAllArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, malformed archive boundaries and partial data
- Exploit idea: force a restore error after selected files are written but before cleanup
- Invariant to test: failed restore must not leave reusable attacker state for later jobs
- Expected Immunefi impact: cross-job state persistence and later job hijack
- Fast validation: interrupt restore mid-stream and confirm residue is removed or quarantined
