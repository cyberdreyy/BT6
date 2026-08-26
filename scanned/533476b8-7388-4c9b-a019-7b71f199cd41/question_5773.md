# Q5773: chain id selects an unauthorized keystore in pipeline_runs_controller.Create

## Question
Can an authenticated node user holding only the 'run' role pick a chain identifier at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) that makes `Create` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Create`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: the resume payload and run id (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `resume payload and run id` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
