# Q0645: DownloadArtifacts duplicate-name overwrite after canonicalization

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `DownloadArtifacts` accept two archive members that canonicalize to the same final path so the second silently replaces a trusted file from the first?

## Target
- File/function: network/gitlab.go: DownloadArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, duplicate names, case variants, canonical aliases
- Exploit idea: rely on canonical-name collisions so later members overwrite earlier trusted files
- Invariant to test: each final restored path must map to at most one archive member
- Expected Immunefi impact: trusted-file overwrite and output tampering
- Fast validation: restore aliases that collapse to one path and assert collision rejection
