# Q0635: artifactDownloadStateFromResponse stale local restore source reused after content changes

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `artifactDownloadStateFromResponse` reuse a stale local archive or extracted residue instead of the object that belongs to the live job?

## Target
- File/function: network/gitlab.go: artifactDownloadStateFromResponse
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, stale local archives, retries, and repeated jobs
- Exploit idea: cause old restore source state to be preferred over the current object
- Invariant to test: restore must bind to the current job object, not stale local residue
- Expected Immunefi impact: cross-job restore confusion and state poisoning
- Fast validation: seed stale local data, repeat the job, and verify it is not consumed
