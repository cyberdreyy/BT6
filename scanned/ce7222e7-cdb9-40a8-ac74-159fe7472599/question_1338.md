# Q1338: handleSecret generated script save or load path is attacker-chosen

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` save or load generated script content through a path the attacker can collide with trusted files?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, script save paths and temp-file names
- Exploit idea: steer script persistence onto a colliding or escaping path
- Invariant to test: generated scripts must only be saved and loaded from isolated temp paths
- Expected Immunefi impact: later-stage hijack or secret-bearing file overwrite
- Fast validation: collide save/load paths and verify generated scripts remain isolated
