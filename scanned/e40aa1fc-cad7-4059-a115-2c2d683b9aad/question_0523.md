# Q0523: writeUploadArtifact helper or temp file inclusion in the archive

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `writeUploadArtifact` include runner temp files, helper outputs, or credential-bearing files that were never meant for user-controlled archive export?

## Target
- File/function: shells/abstract.go: writeUploadArtifact
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, temp-file names and helper-created files
- Exploit idea: cause packaging to include runner-created files that are adjacent to user output
- Invariant to test: runner temp, helper, and credential files must never enter user archives
- Expected Immunefi impact: secret exposure across trust boundaries
- Fast validation: place files near helper/temp paths and verify they are excluded
