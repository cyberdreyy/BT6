# Q1165: per-sender limits keyed on spoofable identity in multihandler.NewMultiHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests evade the per-sender limiter reached by `NewMultiHandler` at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests by rotating an unauthenticated key (address field, IP header, connection), obtaining DON execution beyond entitlement?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `NewMultiHandler`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: legacy vs JSON-RPC envelope choice (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `legacy vs JSON-RPC envelope choice` across requests.
- Invariant to test: limits must key on the cryptographically verified sender
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating the limiter key and asserting throttling still applies
