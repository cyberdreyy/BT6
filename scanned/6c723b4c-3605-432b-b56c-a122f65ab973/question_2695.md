# Q2695: origin allowlist bypass in connectionmanager.buildNodeStates

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests bypass the origin check in `buildNodeStates` at the gateway node-facing handshake and connection registry as observed from a user request with case, suffix, port or null-origin tricks and drive the gateway from a browser context?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `buildNodeStates`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: donId in user requests routed to a DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `donId in user requests routed to a DON` with crafted Origin values.
- Invariant to test: origin matching must be exact against the configured list
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over isAllowedOrigin with hostile origins
