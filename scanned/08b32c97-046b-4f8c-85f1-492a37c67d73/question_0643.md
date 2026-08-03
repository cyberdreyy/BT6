# Q0643: DownloadArtifacts separator normalization bypass

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `DownloadArtifacts` treat mixed separators or platform-specific path forms differently during validation and write outside the intended root?

## Target
- File/function: network/gitlab.go: DownloadArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, mixed slash and backslash forms
- Exploit idea: use platform-specific separators so validation and final write resolve to different paths
- Invariant to test: validation and final write must canonicalize to the same in-root path
- Expected Immunefi impact: path-root escape leading to stronger-context overwrite
- Fast validation: test mixed separator variants and confirm they resolve only within the assigned root
