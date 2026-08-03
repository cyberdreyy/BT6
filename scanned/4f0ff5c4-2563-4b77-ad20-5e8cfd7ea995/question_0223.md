# Q0223: openArchive separator normalization bypass

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `openArchive` treat mixed separators or platform-specific path forms differently during validation and write outside the intended root?

## Target
- File/function: commands/helpers/artifacts_downloader.go: openArchive
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, mixed slash and backslash forms
- Exploit idea: use platform-specific separators so validation and final write resolve to different paths
- Invariant to test: validation and final write must canonicalize to the same in-root path
- Expected Immunefi impact: path-root escape leading to stronger-context overwrite
- Fast validation: test mixed separator variants and confirm they resolve only within the assigned root
