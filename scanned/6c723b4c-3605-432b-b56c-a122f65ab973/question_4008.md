# Q4008: chain id selects an unauthorized keystore in pipeline_runs_controller.Show

## Question
Can an authenticated node user holding only the 'run' role pick a chain identifier at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) that makes `Show` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Show`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: the job ID/external job ID in the path (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `job ID/external job ID in the path` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
