# Q2122: getBuildImage credentials prepared for one registry hit another

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `getBuildImage` prepare credentials for one registry host and then apply them to another after image resolution changes?

## Target
- File/function: executors/docker/docker.go: getBuildImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, image refs, rewritten registry hosts, and auth-related variables
- Exploit idea: change the effective registry after auth state was prepared
- Invariant to test: registry credentials must stay bound to the final resolved registry
- Expected Immunefi impact: token disclosure or unauthorized image access
- Fast validation: use rewritten or aliased registries and verify credentials stay scoped correctly
