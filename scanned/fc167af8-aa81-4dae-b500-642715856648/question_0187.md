# Q0187: getExtractorOptionsFromEnvironment metadata upgrade that turns restored files into trusted code

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `getExtractorOptionsFromEnvironment` preserve executable bits, uid/gid, or timestamps that cause a later stronger-context step to treat attacker content as trusted code or config?

## Target
- File/function: commands/helpers/archive/fastzip/zip_fastzip_extractor.go: getExtractorOptionsFromEnvironment
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, executable mode, uid/gid, and timestamps
- Exploit idea: use metadata restore to upgrade trust of attacker-provided files
- Invariant to test: restored metadata must not elevate trust of attacker content beyond the job boundary
- Expected Immunefi impact: stronger-context execution or protected data exposure
- Fast validation: restore attacker files with hostile metadata and verify no stronger-context trust change occurs
