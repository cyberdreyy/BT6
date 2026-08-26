# Q1868: empty or absent signature accepted in connectionmanager.NewConnectionManager

## Question
Does a request with an empty, zero or absent signature at the gateway node-facing handshake and connection registry as observed from a user request pass through `NewConnectionManager` and receive an identity (zero address) that later checks treat as valid?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `NewConnectionManager`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: donId in user requests routed to a DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `donId in user requests routed to a DON` without signature material.
- Invariant to test: missing signatures must be rejected before identity assignment
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test with empty/zero signatures
