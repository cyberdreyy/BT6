# Q1478: unauthenticated method reachable in connectionmanager.NewConnectionManager

## Question
Is a gateway method routed by `NewConnectionManager` at the gateway node-facing handshake and connection registry as observed from a user request reachable without the authorization the handler assumes, letting any internet client with an arbitrary externally-owned key sending signed gateway requests invoke privileged capability paths?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `NewConnectionManager`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: the claimed address in the handshake (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `claimed address in the handshake` for each advertised method without credentials.
- Invariant to test: every method must declare and enforce its own authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test invoking every method unauthenticated
