# Q0632: artifactDownloadStateFromResponse validated path retargeted before final write

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `artifactDownloadStateFromResponse` validate one destination path but write to another after symlink, rename, or directory replacement races inside the restore tree?

## Target
- File/function: network/gitlab.go: artifactDownloadStateFromResponse
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, restore-tree races and link swaps
- Exploit idea: change the destination after validation but before the final write lands
- Invariant to test: validation and final write must be bound to the same real path
- Expected Immunefi impact: path-root escape or trusted-file overwrite
- Fast validation: race a path swap during restore and verify writes remain confined
