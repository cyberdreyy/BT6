# Q3392: signature verified against the wrong domain in wsserver.handleHealthCheck

## Question
Does `handleHealthCheck` at the public gateway WebSocket endpoint and its auth handshake verify signatures without a domain separator/method tag, letting any internet client with an arbitrary externally-owned key sending signed gateway requests reuse a signature obtained in a different context as gateway authorization?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: frames sent after upgrade (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `frames sent after upgrade` produced for another purpose.
- Invariant to test: signed payloads must be domain-separated per purpose
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test reusing a foreign-domain signature
