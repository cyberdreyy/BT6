# Q2573: authorization key check bypassed in workflow_metadata_handler.Authorize

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests pass the authorization/allowlist check reached by `Authorize` at the workflow metadata/authorization lookup consulted for every user trigger request with a missing, empty or differently-encoded key, obtaining unauthorized DON execution?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `Authorize`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: workflow owner/name/tag claimed in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `workflow owner/name/tag claimed in the request` with the key absent/empty/re-encoded.
- Invariant to test: authorization must fail closed and compare canonicalized values
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over the authorization check with degenerate keys
