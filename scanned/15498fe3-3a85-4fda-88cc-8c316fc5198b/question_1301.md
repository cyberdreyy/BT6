# Q1301: Resolve literal data turns into shell execution

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` turn attacker-controlled text that should stay literal into shell syntax that executes in a stronger runner context?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, characters meaningful to the target shell
- Exploit idea: smuggle shell syntax through quoting or interpolation boundaries
- Invariant to test: resolved secret variables, generated env files, and runtime config consumed by later steps must treat attacker text as data, not executable syntax
- Expected Immunefi impact: stronger-context command execution
- Fast validation: feed shell metacharacters through the entrypoint and verify literal preservation
