# Q0664: downloadArtifactFile symlink pivot to a trusted external path

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `downloadArtifactFile` use symlink entries so writes go through an in-root alias onto a trusted file outside the restore root?

## Target
- File/function: network/gitlab.go: downloadArtifactFile
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, symlink entries and symlink targets
- Exploit idea: place or extract a link first, then write through it during restore
- Invariant to test: restore must not follow attacker-controlled links to destinations outside the root
- Expected Immunefi impact: cross-job tampering or secret exposure through symlink escape
- Fast validation: restore a link-plus-file archive and verify no external target is modified
