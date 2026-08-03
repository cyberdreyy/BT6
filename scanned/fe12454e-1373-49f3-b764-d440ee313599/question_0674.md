# Q0674: downloadArtifactFile archive format mismatch bypasses restore checks

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadArtifactFile` classify one archive format during validation but interpret a different layout during extraction, bypassing containment checks?

## Target
- File/function: network/gitlab.go: downloadArtifactFile
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, ambiguous or malformed archive headers
- Exploit idea: desynchronize format detection from the actual extraction path rules
- Invariant to test: format detection and extraction rules must agree before writing files
- Expected Immunefi impact: path-root escape or trusted-file overwrite
- Fast validation: feed ambiguous archive headers and confirm validation and extraction stay in lockstep
