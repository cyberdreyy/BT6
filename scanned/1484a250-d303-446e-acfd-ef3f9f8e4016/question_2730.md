# Q2730: resolveAuthConfigForImage manifest or inspect state is reused for another image

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `resolveAuthConfigForImage` apply manifest, inspect, or metadata state from one image to a different image after normalization or retries?

## Target
- File/function: executors/docker/internal/pull/manager.go: resolveAuthConfigForImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, image metadata reuse across retries or aliases
- Exploit idea: cross-bind metadata state between image identities
- Invariant to test: image metadata must remain attached to one exact image identity
- Expected Immunefi impact: wrong-image execution or policy bypass
- Fast validation: change image identity after metadata lookup and verify stale metadata is rejected
