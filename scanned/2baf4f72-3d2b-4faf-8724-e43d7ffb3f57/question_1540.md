# Q1540: error/status fields carry raw upstream output in external_initiators.NewExternalInitiatorResource

## Question
Does `NewExternalInitiatorResource` include raw upstream errors or task results at the JSON:API response of /v2/external_initiators that contain secrets or internal endpoints readable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/external_initiators.go](core/web/presenters/external_initiators.go) -> `NewExternalInitiatorResource`
- Entrypoint: the JSON:API response of /v2/external_initiators
- Attacker controls: create vs index route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger a failing run then fetch `create vs index route selection`.
- Invariant to test: rendered errors must be sanitized
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting rendered error fields are sanitized
