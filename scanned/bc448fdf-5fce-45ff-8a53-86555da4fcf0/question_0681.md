# Q0681: determineUploadState wrong local file is bound for upload

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `determineUploadState` bind artifact upload to a local file that no longer matches the file validated for the live job?

## Target
- File/function: network/gitlab.go: determineUploadState
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, local file replacement after selection
- Exploit idea: swap local file identity after selection but before upload reads it
- Invariant to test: artifact upload must remain bound to one exact validated file identity
- Expected Immunefi impact: wrong-file disclosure or artifact tampering
- Fast validation: replace the selected file before upload and verify the transfer is rejected or rebound safely
