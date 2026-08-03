# Q2147: createBuildVolume cleanup removes the wrong volume path

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` clean up one mounted path while leaving the attacker-selected path intact or deleting another job path?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, cleanup timing and colliding local paths
- Exploit idea: redirect cleanup to the wrong local mount target
- Invariant to test: cleanup must remove only current job-owned mounted state
- Expected Immunefi impact: persistent hostile state or cross-job tampering
- Fast validation: race cleanup with colliding paths and verify only the intended mount is removed
