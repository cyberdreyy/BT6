# Q0528: writeUploadArtifact link entries preserved for later restore escape

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `writeUploadArtifact` preserve link metadata that a downstream restore follows out of its assigned root?

## Target
- File/function: shells/abstract.go: writeUploadArtifact
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, symlink and hardlink metadata
- Exploit idea: ship link semantics that become dangerous only when a later job restores them
- Invariant to test: user archives must not embed link behavior that breaks restore containment
- Expected Immunefi impact: cross-job path escape via poisoned archive
- Fast validation: archive link entries and verify downstream consumers do not get a root escape
