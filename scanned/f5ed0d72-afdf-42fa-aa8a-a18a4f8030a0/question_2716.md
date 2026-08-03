# Q2716: getImageUsingPullPolicy imported or loaded image collides with a trusted image

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `getImageUsingPullPolicy` load or import an image under a name that collides with a trusted existing image selection?

## Target
- File/function: executors/docker/internal/pull/manager.go: getImageUsingPullPolicy
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, loaded or imported image names
- Exploit idea: reuse trusted local names for attacker-controlled image content
- Invariant to test: imported image identity must not collide with trusted selected images
- Expected Immunefi impact: wrong-image execution or image-state hijack
- Fast validation: load colliding image names and verify trusted images are not replaced implicitly
