# Q5276: length/alignment helper mismatch in wsconnection.ReadChannel

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a string/byte field at an established gateway WebSocket connection whose alignment or length handling in `ReadChannel` makes the parsed value differ from the signed value?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `ReadChannel`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: concurrent connections claiming the same identity (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `concurrent connections claiming the same identity` with padding, embedded NULs or non-UTF8 bytes.
- Invariant to test: encode/decode must round-trip exactly for every input
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: round-trip fuzz-style table test over the alignment helpers
