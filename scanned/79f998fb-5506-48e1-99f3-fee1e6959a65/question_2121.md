# Q2121: getBuildImage normalized image refs share one auth scope

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `getBuildImage` collapse two distinct image or registry references into one auth scope after normalization?

## Target
- File/function: executors/docker/docker.go: getBuildImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, visually similar image refs and registry aliases
- Exploit idea: make one auth decision cover a different final image principal
- Invariant to test: registry auth scope must remain exact for the final selected image
- Expected Immunefi impact: credential disclosure or wrong-image execution
- Fast validation: use equivalent-looking image refs and verify auth does not cross them
