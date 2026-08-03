# Q2711: getImageUsingPullPolicy retry reuses prior auth or manifest state

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `getImageUsingPullPolicy` retry image resolution using auth or manifest state from a previous logical image target?

## Target
- File/function: executors/docker/internal/pull/manager.go: getImageUsingPullPolicy
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, target changes across image retries
- Exploit idea: carry prior target state into a later image retry
- Invariant to test: image retries must remain bound to one logical target
- Expected Immunefi impact: wrong-image execution or cross-registry auth reuse
- Fast validation: change the logical image target during retries and verify rebinding or rejection
