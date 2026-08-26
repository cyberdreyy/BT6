# Q2880: per-sender limits keyed on spoofable identity in wsserver.handleHealthCheck

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests evade the per-sender limiter reached by `handleHealthCheck` at the public gateway WebSocket endpoint and its auth handshake by rotating an unauthenticated key (address field, IP header, connection), obtaining DON execution beyond entitlement?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: frames sent after upgrade (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `frames sent after upgrade` across requests.
- Invariant to test: limits must key on the cryptographically verified sender
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating the limiter key and asserting throttling still applies
