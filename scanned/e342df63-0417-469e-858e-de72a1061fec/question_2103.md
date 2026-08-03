# Q2103: expandAndGetDockerImage local image reuse crosses trust boundaries

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `expandAndGetDockerImage` reuse a locally cached image from an unprotected or unrelated job in a stronger-trust job?

## Target
- File/function: executors/docker/docker.go: expandAndGetDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, local images and repeated jobs
- Exploit idea: let cached image state outlive the trust boundary that created it
- Invariant to test: cached image trust must remain bound to the creating job boundary
- Expected Immunefi impact: protected-job escalation or wrong-image execution
- Fast validation: seed lower-trust local images and verify higher-trust jobs do not consume them unexpectedly
