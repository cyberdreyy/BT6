# Q4144: listing renders objects across owners in job.NewPipelineSpec

## Question
Does the collection built by `NewPipelineSpec` at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID render objects outside an authenticated node user holding only the 'view' role's entitlement together with their sensitive attributes?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewPipelineSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: the job type whose spec presenter is selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `job type whose spec presenter is selected` as a low-role user.
- Invariant to test: collections must be filtered before rendering
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing collection contents per role
