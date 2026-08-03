# Q0659: DownloadArtifacts manifest or checksum bound to the wrong object

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `DownloadArtifacts` trust manifest or checksum state derived from one object while extracting another object to disk?

## Target
- File/function: network/gitlab.go: DownloadArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, object changes between validation and extraction
- Exploit idea: validate one archive version and then swap to another before extraction completes
- Invariant to test: integrity metadata must stay bound to the exact extracted object
- Expected Immunefi impact: trusted-file overwrite or output tampering
- Fast validation: swap source objects after validation and verify extraction is rejected
