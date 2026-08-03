# Q0609: UploadRawArtifacts artifact metadata leaks protected path details

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `UploadRawArtifacts` emit or trust artifact metadata that reveals hidden paths, protected filenames, or trust-boundary details?

## Target
- File/function: network/gitlab.go: UploadRawArtifacts
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, artifact metadata and generated side state
- Exploit idea: surface sensitive artifact-path details through metadata handling
- Invariant to test: artifact metadata must not reveal protected path or runner-private details
- Expected Immunefi impact: secret or internal-path disclosure
- Fast validation: inspect artifact metadata and verify protected path details are not exposed
