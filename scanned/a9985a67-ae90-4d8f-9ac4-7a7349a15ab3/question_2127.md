# Q2127: getBuildImage wrong registry wins auth selection

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `getBuildImage` prefer auth for a different registry than the one that owns the final selected image?

## Target
- File/function: executors/docker/docker.go: getBuildImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, multiple auth sources and similar registry refs
- Exploit idea: confuse auth-source precedence so the wrong registry config is chosen
- Invariant to test: auth selection must match the exact final image registry
- Expected Immunefi impact: credential misuse or image-pull confusion
- Fast validation: provide overlapping auth sources and verify exact registry matching
