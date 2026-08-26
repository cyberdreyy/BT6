# Q1783: signature verified against the wrong domain in wsconnection.NewWSConnectionWrapper

## Question
Does `NewWSConnectionWrapper` at an established gateway WebSocket connection verify signatures without a domain separator/method tag, letting any internet client with an arbitrary externally-owned key sending signed gateway requests reuse a signature obtained in a different context as gateway authorization?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `NewWSConnectionWrapper`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: connection reset/reconnect timing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `connection reset/reconnect timing` produced for another purpose.
- Invariant to test: signed payloads must be domain-separated per purpose
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test reusing a foreign-domain signature
