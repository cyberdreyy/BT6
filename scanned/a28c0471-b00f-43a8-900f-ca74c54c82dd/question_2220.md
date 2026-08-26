# Q2220: export bundle rendered to a non-owner in job.NewOffChainReportingSpec

## Question
Does `NewOffChainReportingSpec` render exported key material at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID to any caller passing the role gate rather than the key owner/admin only?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewOffChainReportingSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: the job type whose spec presenter is selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `job type whose spec presenter is selected` from the weakest role accepted.
- Invariant to test: export material may only be rendered to an admin-authenticated owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test requesting the export from each role
