# Q2788: Get build, helper, and service images blur together

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `Get` let image resolution blur build, helper, and service image identity so one role runs the wrong image?

## Target
- File/function: helpers/docker/auth/auth.go: Get
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, build, helper, and service image refs
- Exploit idea: collapse role-specific images into one incorrectly trusted image state
- Invariant to test: build, helper, and service image identity must stay separate
- Expected Immunefi impact: wrong-role execution or secret exposure
- Fast validation: use overlapping image refs and verify role images remain distinct
