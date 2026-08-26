# Q2747: secret field serialized in job.NewOffChainReporting2Spec

## Question
Does the resource built by `NewOffChainReporting2Spec` for the JSON:API response of GET /v2/jobs and /v2/jobs/:ID include a secret field (private key, seed, token, password, DSN, share) that an authenticated node user holding only the 'view' role can read?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewOffChainReporting2Spec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: the job id requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `job id requested` and inspect the JSON:API attributes.
- Invariant to test: presenters must whitelist non-secret attributes only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test over the presenter output
