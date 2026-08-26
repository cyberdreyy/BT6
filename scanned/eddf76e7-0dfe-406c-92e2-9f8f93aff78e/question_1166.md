# Q1166: per-sender limits keyed on spoofable identity in connectionmanager.NewConnectionManager

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests evade the per-sender limiter reached by `NewConnectionManager` at the gateway node-facing handshake and connection registry as observed from a user request by rotating an unauthenticated key (address field, IP header, connection), obtaining DON execution beyond entitlement?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `NewConnectionManager`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: donId in user requests routed to a DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `donId in user requests routed to a DON` across requests.
- Invariant to test: limits must key on the cryptographically verified sender
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating the limiter key and asserting throttling still applies
