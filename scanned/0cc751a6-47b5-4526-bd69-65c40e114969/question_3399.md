# Q3399: signature verified against the wrong domain in connectionmanager.buildNodeStates

## Question
Does `buildNodeStates` at the gateway node-facing handshake and connection registry as observed from a user request verify signatures without a domain separator/method tag, letting any internet client with an arbitrary externally-owned key sending signed gateway requests reuse a signature obtained in a different context as gateway authorization?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `buildNodeStates`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: handshake timing and repetition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `handshake timing and repetition` produced for another purpose.
- Invariant to test: signed payloads must be domain-separated per purpose
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test reusing a foreign-domain signature
