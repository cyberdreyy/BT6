# Q2757: pullDockerImage container is created from a different image than inspected

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `pullDockerImage` inspect one image identity and then create the workload from another image due to stale or rebound selection state?

## Target
- File/function: executors/docker/internal/pull/manager.go: pullDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, inspect-before-run flows and mutable image selection
- Exploit idea: desynchronize inspection from final creation
- Invariant to test: inspected image identity and created image identity must match exactly
- Expected Immunefi impact: wrong-image execution or policy bypass
- Fast validation: change selected images after inspection and verify creation does not drift
