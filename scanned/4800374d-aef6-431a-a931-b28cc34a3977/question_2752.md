# Q2752: pullDockerImage image selection changes after auth preparation

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `pullDockerImage` change the final image target after auth state was prepared and still keep the prior trusted auth decision?

## Target
- File/function: executors/docker/internal/pull/manager.go: pullDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, late image target changes
- Exploit idea: prepare trusted auth for one image then run another
- Invariant to test: auth state must be recomputed whenever the final image changes
- Expected Immunefi impact: credential misuse or wrong-image execution
- Fast validation: change the final image after auth preparation and verify the flow rebinds or fails
