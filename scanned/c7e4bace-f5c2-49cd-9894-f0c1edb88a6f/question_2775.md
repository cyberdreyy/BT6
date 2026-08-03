# Q2775: imagePullOnce auth error paths leak higher-trust details

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `imagePullOnce` surface higher-trust registry or helper details through attacker-visible auth or image error paths?

## Target
- File/function: executors/docker/internal/pull/manager.go: imagePullOnce
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, auth failures and error-visible image context
- Exploit idea: route sensitive registry or helper context into attacker-visible failures
- Invariant to test: auth and image error handling must not disclose protected secrets or paths
- Expected Immunefi impact: secret exposure through error handling
- Fast validation: force auth failures and verify sensitive details are not disclosed
