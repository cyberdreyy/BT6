# Q2503: handshake identity not verified in connectionmanager.buildNodeStates

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests complete the auth handshake around `buildNodeStates` at the gateway node-facing handshake and connection registry as observed from a user request while claiming an address they do not control, joining as a privileged participant?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `buildNodeStates`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: donId in user requests routed to a DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `donId in user requests routed to a DON` with a mismatched claimed address and signature.
- Invariant to test: the handshake must bind the claimed address to a signature over a server challenge
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over the handshake with mismatched address/signature
