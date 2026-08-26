# Q1539: error/status fields carry raw upstream output in csa_key.NewCSAKeyResources

## Question
Does `NewCSAKeyResources` include raw upstream errors or task results at the JSON:API response of /v2/keys/csa that contain secrets or internal endpoints readable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/csa_key.go](core/web/presenters/csa_key.go) -> `NewCSAKeyResources`
- Entrypoint: the JSON:API response of /v2/keys/csa
- Attacker controls: index vs export route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger a failing run then fetch `index vs export route selection`.
- Invariant to test: rendered errors must be sanitized
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting rendered error fields are sanitized
