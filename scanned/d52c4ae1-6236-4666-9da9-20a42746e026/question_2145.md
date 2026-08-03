# Q2145: createBuildVolume stale mounted residue survives into later jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` leave lower-trust mounted residue behind so a later protected or unrelated job reuses it?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, repeated jobs and stale mount state
- Exploit idea: persist mounted state beyond the current job boundary
- Invariant to test: mounted state must be cleaned before later jobs can see it
- Expected Immunefi impact: cross-job state persistence or protected-boundary break
- Fast validation: leave hostile mounted state behind and verify later jobs do not consume it
