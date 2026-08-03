# Q0422: artifactFilename relative-path inclusion outside the assigned root

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `artifactFilename` walk or package paths that escape the assigned workspace root via relative traversal or base-path confusion?

## Target
- File/function: commands/helpers/artifacts_uploader.go: artifactFilename
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, relative paths and base-path confusion
- Exploit idea: select paths that validate relative to one base but read from another
- Invariant to test: artifact or cache packaging must remain inside the assigned workspace root
- Expected Immunefi impact: secret exposure or archive poisoning
- Fast validation: archive relative traversal candidates and assert only in-root files are included
