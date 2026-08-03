# Q2685: GetDockerImage pull state trusts a stale selected image

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `GetDockerImage` reuse stale pull or manifest state for an image that no longer matches the current job input?

## Target
- File/function: executors/docker/internal/pull/manager.go: GetDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, retries, repeated jobs, and stale pull state
- Exploit idea: carry selected-image state into a later logical image selection
- Invariant to test: pull and manifest state must remain bound to the current image input
- Expected Immunefi impact: wrong-image execution or stale-state reuse
- Fast validation: repeat image resolution with changed input and verify stale pull state is discarded
