# Q5950: identifier reveals sensitive identity in job.NewVRFSpec

## Question
Does the identifier or metadata rendered by `NewVRFSpec` at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID reveal key identities, addresses or credential fingerprints that let an authenticated node user holding only the 'view' role target key theft or fund movement?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewVRFSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: pipeline spec fields returned (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `pipeline spec fields returned` at the lowest role.
- Invariant to test: identity metadata must be limited to what the caller's role needs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing rendered identifiers per role
