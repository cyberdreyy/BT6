# Q2789: Get auth config precedence prefers attacker state

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `Get` prefer attacker-controlled auth state over the trusted auth source for the final image?

## Target
- File/function: helpers/docker/auth/auth.go: Get
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, multiple auth sources and precedence rules
- Exploit idea: abuse precedence so lower-trust auth config wins selection
- Invariant to test: auth precedence must not let lower-trust state replace the intended registry config
- Expected Immunefi impact: credential misuse or wrong-image access
- Fast validation: provide conflicting auth sources and verify trusted precedence wins
