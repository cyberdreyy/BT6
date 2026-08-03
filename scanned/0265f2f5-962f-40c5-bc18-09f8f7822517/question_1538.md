# Q1538: cleanPath generated script save or load path is attacker-chosen

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `cleanPath` save or load generated script content through a path the attacker can collide with trusted files?

## Target
- File/function: shells/powershell.go: cleanPath
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, script save paths and temp-file names
- Exploit idea: steer script persistence onto a colliding or escaping path
- Invariant to test: generated scripts must only be saved and loaded from isolated temp paths
- Expected Immunefi impact: later-stage hijack or secret-bearing file overwrite
- Fast validation: collide save/load paths and verify generated scripts remain isolated
