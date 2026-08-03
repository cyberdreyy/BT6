# Q2206: createVolumes path normalization aliases one mount onto another

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` treat two host paths as different during validation but as the same mounted path during final use?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, slash, backslash, case, or drive aliases
- Exploit idea: pass validation on one path spelling and mount another equivalent path
- Invariant to test: validation and final mount path must agree on one canonical root
- Expected Immunefi impact: cross-job tampering or path-root escape
- Fast validation: exercise path aliases and verify consistent canonicalization
