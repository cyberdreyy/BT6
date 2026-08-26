# Q0452: length/alignment helper mismatch in handshake.PackAuthHeader

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a string/byte field at the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange) whose alignment or length handling in `PackAuthHeader` makes the parsed value differ from the signed value?

## Target
- File/function: [core/services/gateway/network/handshake.go](core/services/gateway/network/handshake.go) -> `PackAuthHeader`
- Entrypoint: the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange)
- Attacker controls: the challenge bytes echoed back (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `challenge bytes echoed back` with padding, embedded NULs or non-UTF8 bytes.
- Invariant to test: encode/decode must round-trip exactly for every input
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: round-trip fuzz-style table test over the alignment helpers
