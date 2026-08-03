# Q0206: updateFileMetadata case-fold or alias collision across platforms

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `updateFileMetadata` emit or restore names that differ only by case or platform aliasing so one runner view looks safe but the final path collides on another path layer?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_extractor.go: updateFileMetadata
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, case variants and platform aliases
- Exploit idea: cause validation on one representation and write on another equivalent representation
- Invariant to test: platform-specific aliases must not let two names resolve to one trusted destination
- Expected Immunefi impact: cross-job file overwrite or cache/artifact confusion
- Fast validation: exercise case-folded aliases and verify deterministic rejection
