# Q0651: DownloadArtifacts sibling build or cache directory overwrite

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `DownloadArtifacts` write into a sibling build or cache directory for another job on the same runner?

## Target
- File/function: network/gitlab.go: DownloadArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, names targeting sibling build/cache directories
- Exploit idea: escape into another assigned runner directory rather than the current job root
- Invariant to test: one job restore must not modify another job directory on the same runner
- Expected Immunefi impact: cross-project or cross-job state tampering
- Fast validation: attempt sibling-directory writes and verify isolation
