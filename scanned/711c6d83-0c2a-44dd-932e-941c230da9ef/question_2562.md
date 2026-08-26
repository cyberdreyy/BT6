# Q2562: challenge reuse or predictability in handshake.UnpackSignedAuthHeader

## Question
Is the challenge produced/validated by `UnpackSignedAuthHeader` at the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange) predictable, reusable or unbound to the connection, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay a captured handshake response?

## Target
- File/function: [core/services/gateway/network/handshake.go](core/services/gateway/network/handshake.go) -> `UnpackSignedAuthHeader`
- Entrypoint: the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange)
- Attacker controls: the challenge bytes echoed back (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `challenge bytes echoed back` captured from another handshake.
- Invariant to test: challenges must be random, single-use and connection-bound
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a handshake response
