# Q0028: route role weaker than the side effect in pipeline_runs_controller.Index

## Question
Is the route reaching `Index` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) gated by a role weaker than the effect it produces, letting an authenticated node user holding only the 'run' role cause it?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Index`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: the job ID/external job ID in the path (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `job ID/external job ID in the path` from the weakest session the route accepts.
- Invariant to test: the route gate must match the strongest side effect of the handler
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test invoking the handler at each role level
