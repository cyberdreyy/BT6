# Q0550: writeUploadArtifacts untracked enumeration picks up sensitive files

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `writeUploadArtifacts` enumerate untracked or generated files broadly enough to capture secrets, temp files, or helper outputs?

## Target
- File/function: shells/abstract.go: writeUploadArtifacts
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, untracked files and generated files
- Exploit idea: place sensitive runner-adjacent files where broad enumeration picks them up
- Invariant to test: enumeration must be limited to files the job is meant to archive
- Expected Immunefi impact: secret exposure across job boundaries
- Fast validation: populate untracked files near sensitive paths and confirm they are not packaged
