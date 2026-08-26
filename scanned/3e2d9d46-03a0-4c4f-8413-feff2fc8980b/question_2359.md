# Q2359: spec presenter echoes credentials in job.NewOffChainReportingSpec

## Question
Does the spec rendered by `NewOffChainReportingSpec` at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID include embedded credentials (bridge tokens, URLs with basic auth, initiator secrets, webhook tokens) submitted at creation time?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewOffChainReportingSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: the job id requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create an object with a credential-bearing field then fetch `job id requested`.
- Invariant to test: credential-bearing spec fields must be redacted on read
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: round-trip test creating with credentials and asserting redaction on read
