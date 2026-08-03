# Q0506: downloadAllArtifacts case-fold or alias collision across platforms

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadAllArtifacts` emit or restore names that differ only by case or platform aliasing so one runner view looks safe but the final path collides on another path layer?

## Target
- File/function: shells/abstract.go: downloadAllArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, case variants and platform aliases
- Exploit idea: cause validation on one representation and write on another equivalent representation
- Invariant to test: platform-specific aliases must not let two names resolve to one trusted destination
- Expected Immunefi impact: cross-job file overwrite or cache/artifact confusion
- Fast validation: exercise case-folded aliases and verify deterministic rejection
