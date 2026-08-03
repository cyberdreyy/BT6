# Q0558: writeUploadArtifacts hidden files bypass include or exclude assumptions

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `writeUploadArtifacts` include hidden files or dotfiles that are treated differently from visibly equivalent paths during filtering?

## Target
- File/function: shells/abstract.go: writeUploadArtifacts
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, dotfiles and hidden names
- Exploit idea: hide dangerous content behind special-looking names that skip normal filtering paths
- Invariant to test: hidden names must follow the same inclusion and exclusion invariants as visible names
- Expected Immunefi impact: secret exposure or later restore poisoning
- Fast validation: test hidden-name variants and confirm filtering stays consistent
