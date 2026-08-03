# Q2790: Get manifest or inspect state is reused for another image

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `Get` apply manifest, inspect, or metadata state from one image to a different image after normalization or retries?

## Target
- File/function: helpers/docker/auth/auth.go: Get
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, image metadata reuse across retries or aliases
- Exploit idea: cross-bind metadata state between image identities
- Invariant to test: image metadata must remain attached to one exact image identity
- Expected Immunefi impact: wrong-image execution or policy bypass
- Fast validation: change image identity after metadata lookup and verify stale metadata is rejected
