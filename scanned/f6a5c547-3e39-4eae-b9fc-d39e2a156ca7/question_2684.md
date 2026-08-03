# Q2684: GetDockerImage tag or digest changes after validation

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `GetDockerImage` validate one image identity and then run another after tag or digest state changes later in the flow?

## Target
- File/function: executors/docker/internal/pull/manager.go: GetDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, mutable tags and late image resolution
- Exploit idea: separate image validation from the final image used to create the workload
- Invariant to test: validated image identity and executed image identity must match exactly
- Expected Immunefi impact: wrong-image execution or image-policy bypass
- Fast validation: change tag state after validation and verify the executed image does not drift
