# Q0450: length/alignment helper mismatch in wsserver.NewWebSocketServer

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a string/byte field at the public gateway WebSocket endpoint and its auth handshake whose alignment or length handling in `NewWebSocketServer` makes the parsed value differ from the signed value?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `NewWebSocketServer`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: the challenge response signature (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `challenge response signature` with padding, embedded NULs or non-UTF8 bytes.
- Invariant to test: encode/decode must round-trip exactly for every input
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: round-trip fuzz-style table test over the alignment helpers
