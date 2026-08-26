# Q4600: authorization key check bypassed in message_util.ValidatedResponseFromMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests pass the authorization/allowlist check reached by `ValidatedResponseFromMessage` at validated conversion between gateway messages, requests and responses with a missing, empty or differently-encoded key, obtaining unauthorized DON execution?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedResponseFromMessage`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: encoding variants of identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `encoding variants of identifiers` with the key absent/empty/re-encoded.
- Invariant to test: authorization must fail closed and compare canonicalized values
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over the authorization check with degenerate keys
