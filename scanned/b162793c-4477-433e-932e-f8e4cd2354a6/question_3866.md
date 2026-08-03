# Q3866: uploadRawArtifactsQuery artifact object changes after validation

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `uploadRawArtifactsQuery` validate one artifact object and then continue download or upload against another object after state changes?

## Target
- File/function: network/gitlab.go: uploadRawArtifactsQuery
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, late object changes and retries
- Exploit idea: separate artifact validation from the final object used in transfer
- Invariant to test: validated artifact identity and transferred artifact identity must match exactly
- Expected Immunefi impact: artifact hijack or wrong-object transfer
- Fast validation: change artifact objects after validation and verify the transfer does not drift
