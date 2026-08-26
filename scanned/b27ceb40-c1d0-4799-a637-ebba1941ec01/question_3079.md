# Q3079: validation happens after dispatch in connectionmanager.buildNodeStates

## Question
Does `buildNodeStates` at the gateway node-facing handshake and connection registry as observed from a user request dispatch the request to the DON before validation completes, so any internet client with an arbitrary externally-owned key sending signed gateway requests's invalid request still consumes DON work or reaches capability code?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `buildNodeStates`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: donId in user requests routed to a DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `donId in user requests routed to a DON` that fails late in validation.
- Invariant to test: no dispatch may precede complete validation
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test asserting no node message is sent for invalid requests
