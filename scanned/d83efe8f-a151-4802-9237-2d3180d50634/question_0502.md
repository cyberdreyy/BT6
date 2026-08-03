# Q0502: downloadAllArtifacts absolute-path restore outside the assigned root

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadAllArtifacts` accept absolute or drive-qualified paths that bypass the assigned restore root and replace stronger-context files?

## Target
- File/function: shells/abstract.go: downloadAllArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, absolute paths, drive paths, UNC-style paths
- Exploit idea: feed absolute destination aliases that are trusted after path cleaning
- Invariant to test: absolute or drive-qualified member names must never escape the downstream job root
- Expected Immunefi impact: cross-job state tampering or stronger-context file overwrite
- Fast validation: restore entries using absolute-style names and verify the operation is rejected or confined
