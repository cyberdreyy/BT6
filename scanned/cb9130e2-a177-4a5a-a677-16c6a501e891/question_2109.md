# Q2109: expandAndGetDockerImage auth config precedence prefers attacker state

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `expandAndGetDockerImage` prefer attacker-controlled auth state over the trusted auth source for the final image?

## Target
- File/function: executors/docker/docker.go: expandAndGetDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, multiple auth sources and precedence rules
- Exploit idea: abuse precedence so lower-trust auth config wins selection
- Invariant to test: auth precedence must not let lower-trust state replace the intended registry config
- Expected Immunefi impact: credential misuse or wrong-image access
- Fast validation: provide conflicting auth sources and verify trusted precedence wins
