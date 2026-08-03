# Q1323: handleSecret arg expansion evaluates attacker syntax

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` apply argument expansion where literal passing was required, turning attacker-controlled content into shell or helper syntax?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, arguments that contain shell-significant sequences
- Exploit idea: route literal-looking input through an expansion path
- Invariant to test: argument construction must preserve literal meaning across shells
- Expected Immunefi impact: stronger-context command execution or wrong-target helper invocation
- Fast validation: pass expansion-sensitive arguments and verify they stay literal end to end
