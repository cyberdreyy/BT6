# Q0524: writeUploadArtifact repo-control file inclusion

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `writeUploadArtifact` package `.git`, hook, or config files that later restore into trusted repo-control paths?

## Target
- File/function: shells/abstract.go: writeUploadArtifact
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, repo-control files and hidden paths
- Exploit idea: archive control files that alter later git or helper behavior after restore
- Invariant to test: repo-control files must not be exported into trust-boundary-crossing archives
- Expected Immunefi impact: protected-ref escalation or credential misuse
- Fast validation: include repo-control files and confirm packaging rejects or strips them
