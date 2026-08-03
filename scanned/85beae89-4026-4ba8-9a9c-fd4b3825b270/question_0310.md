# Q0310: Archive untracked enumeration picks up sensitive files

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` enumerate untracked or generated files broadly enough to capture secrets, temp files, or helper outputs?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, untracked files and generated files
- Exploit idea: place sensitive runner-adjacent files where broad enumeration picks them up
- Invariant to test: enumeration must be limited to files the job is meant to archive
- Expected Immunefi impact: secret exposure across job boundaries
- Fast validation: populate untracked files near sensitive paths and confirm they are not packaged
