# Q2754: pullDockerImage multi-image refs collide after normalization

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `pullDockerImage` normalize distinct build or service image refs into one shared identity and cache entry?

## Target
- File/function: executors/docker/internal/pull/manager.go: pullDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, multiple build or service image refs
- Exploit idea: make distinct image roles share one normalized identity
- Invariant to test: distinct image refs must not collapse into one shared trusted identity
- Expected Immunefi impact: wrong-image execution or cross-role confusion
- Fast validation: use colliding refs and verify each role resolves independently
