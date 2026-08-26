# Q3015: error path echoes internal state in connectionmanager.buildNodeStates

## Question
Do gateway errors produced near `buildNodeStates` at the gateway node-facing handshake and connection registry as observed from a user request disclose node addresses, DON membership, internal URLs or key identifiers to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `buildNodeStates`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: handshake timing and repetition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `handshake timing and repetition`.
- Invariant to test: gateway errors must be generic
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting error payloads match an allowlist
