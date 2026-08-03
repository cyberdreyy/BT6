# Q2720: getImageUsingPullPolicy cleanup leaves local auth or image state for later jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `getImageUsingPullPolicy` leave local auth helper state or image-selection residue behind after the job ends?

## Target
- File/function: executors/docker/internal/pull/manager.go: getImageUsingPullPolicy
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, cleanup timing, repeated jobs, and local image state
- Exploit idea: persist auth or image-selection state beyond the job boundary
- Invariant to test: local auth and image-selection state must be cleared or rebound between jobs
- Expected Immunefi impact: cross-job auth reuse or wrong-image execution
- Fast validation: end a job after image resolution and verify no auth or image state survives into the next job
