# Q2733: resolveAuthConfigForImage cached image metadata from lower trust reaches protected jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `resolveAuthConfigForImage` reuse cached metadata or trust state from an unprotected image selection in a protected job?

## Target
- File/function: executors/docker/internal/pull/manager.go: resolveAuthConfigForImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, protected and unprotected jobs plus local image metadata
- Exploit idea: carry image trust state across a protection boundary
- Invariant to test: image metadata trust must stay bound to the originating trust boundary
- Expected Immunefi impact: protected-job escalation or wrong-image execution
- Fast validation: seed lower-trust metadata and verify protected jobs do not inherit it
