# Q2118: expandAndGetDockerImage registry URL aliases cross auth boundaries

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `expandAndGetDockerImage` treat distinct registry URLs or paths as equivalent for auth despite different security principals?

## Target
- File/function: executors/docker/docker.go: expandAndGetDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, registry URL aliases and path variants
- Exploit idea: widen auth scope through URL normalization
- Invariant to test: registry auth must remain attached to the exact final registry principal
- Expected Immunefi impact: credential disclosure or unauthorized image access
- Fast validation: use registry URL aliases and verify auth does not cross principals
