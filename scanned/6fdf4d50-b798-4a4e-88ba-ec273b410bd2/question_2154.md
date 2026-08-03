# Q2154: createBuildVolume volume removal targets the wrong identity

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` remove or detach a different volume identity than the one selected for the current job?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, colliding local names and concurrent cleanup
- Exploit idea: exploit name or path ambiguity so removal lands on the wrong target
- Invariant to test: volume removal must stay bound to the current job volume identity
- Expected Immunefi impact: cross-job disruption or persistent hostile state
- Fast validation: create colliding volume identities and verify removal does not cross jobs
