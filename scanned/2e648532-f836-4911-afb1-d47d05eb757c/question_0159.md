# Q0159: Extract manifest or checksum bound to the wrong object

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `Extract` trust manifest or checksum state derived from one object while extracting another object to disk?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_extractor.go: Extract
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, object changes between validation and extraction
- Exploit idea: validate one archive version and then swap to another before extraction completes
- Invariant to test: integrity metadata must stay bound to the exact extracted object
- Expected Immunefi impact: trusted-file overwrite or output tampering
- Fast validation: swap source objects after validation and verify extraction is rejected
