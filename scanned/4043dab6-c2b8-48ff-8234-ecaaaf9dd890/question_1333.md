# Q1333: handleSecret after-script or cleanup executes attacker syntax

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` build after-script or cleanup commands that execute attacker-controlled syntax in a stronger or differently trusted phase?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, values reused in after_script or cleanup generation
- Exploit idea: smuggle execution into a later runner-generated phase
- Invariant to test: later phases must preserve literal values and trust boundaries
- Expected Immunefi impact: stronger-context command execution
- Fast validation: inject shell syntax into later-phase inputs and verify literal preservation
