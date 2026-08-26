# Q0767: challenge reuse or predictability in wsconnection.NewWSConnectionWrapper

## Question
Is the challenge produced/validated by `NewWSConnectionWrapper` at an established gateway WebSocket connection predictable, reusable or unbound to the connection, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay a captured handshake response?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `NewWSConnectionWrapper`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: frame contents and framing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `frame contents and framing` captured from another handshake.
- Invariant to test: challenges must be random, single-use and connection-bound
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a handshake response
