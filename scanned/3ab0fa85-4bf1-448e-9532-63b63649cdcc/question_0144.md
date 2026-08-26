# Q0144: authorization key check bypassed in handler.UserCallbackPayload

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests pass the authorization/allowlist check reached by `UserCallbackPayload` at the gateway handler interface boundary every public user request passes through with a missing, empty or differently-encoded key, obtaining unauthorized DON execution?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `UserCallbackPayload`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: callback correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `callback correlation fields` with the key absent/empty/re-encoded.
- Invariant to test: authorization must fail closed and compare canonicalized values
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over the authorization check with degenerate keys
