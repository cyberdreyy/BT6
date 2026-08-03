# Q0195: getExtractorOptionsFromEnvironment stale local restore source reused after content changes

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `getExtractorOptionsFromEnvironment` reuse a stale local archive or extracted residue instead of the object that belongs to the live job?

## Target
- File/function: commands/helpers/archive/fastzip/zip_fastzip_extractor.go: getExtractorOptionsFromEnvironment
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, stale local archives, retries, and repeated jobs
- Exploit idea: cause old restore source state to be preferred over the current object
- Invariant to test: restore must bind to the current job object, not stale local residue
- Expected Immunefi impact: cross-job restore confusion and state poisoning
- Fast validation: seed stale local data, repeat the job, and verify it is not consumed
