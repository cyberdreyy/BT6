# Q0165: Extract duplicate-name overwrite after canonicalization

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `Extract` accept two archive members that canonicalize to the same final path so the second silently replaces a trusted file from the first?

## Target
- File/function: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go: Extract
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, duplicate names, case variants, canonical aliases
- Exploit idea: rely on canonical-name collisions so later members overwrite earlier trusted files
- Invariant to test: each final restored path must map to at most one archive member
- Expected Immunefi impact: trusted-file overwrite and output tampering
- Fast validation: restore aliases that collapse to one path and assert collision rejection
