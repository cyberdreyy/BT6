# Q5774: chain id selects an unauthorized keystore in jobs_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pick a chain identifier at POST/PATCH /v2/jobs (edit role) that makes `Create` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Create`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: bridge names and external job id (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge names and external job id` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
