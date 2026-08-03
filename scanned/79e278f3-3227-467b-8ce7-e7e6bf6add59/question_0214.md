# Q0214: updateFileMetadata archive format mismatch bypasses restore checks

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `updateFileMetadata` classify one archive format during validation but interpret a different layout during extraction, bypassing containment checks?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_extractor.go: updateFileMetadata
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, ambiguous or malformed archive headers
- Exploit idea: desynchronize format detection from the actual extraction path rules
- Invariant to test: format detection and extraction rules must agree before writing files
- Expected Immunefi impact: path-root escape or trusted-file overwrite
- Fast validation: feed ambiguous archive headers and confirm validation and extraction stay in lockstep
