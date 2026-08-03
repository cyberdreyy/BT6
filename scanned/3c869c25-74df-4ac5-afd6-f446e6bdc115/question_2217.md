# Q2217: createVolumes lower-trust mounted state reaches a protected job

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` carry mounted state from an unprotected or unrelated job into a later protected job?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, protected and unprotected refs plus repeated jobs
- Exploit idea: persist mounted state across a protection boundary
- Invariant to test: mounted state must be cleared or re-created when the protection boundary changes
- Expected Immunefi impact: protected-job escalation through mounted-state reuse
- Fast validation: seed unprotected mounted state and verify a protected job does not inherit it
