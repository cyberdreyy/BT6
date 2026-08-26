# Q3841: length/alignment helper mismatch in connectionmanager.DONConnectionManager

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a string/byte field at the gateway node-facing handshake and connection registry as observed from a user request whose alignment or length handling in `DONConnectionManager` makes the parsed value differ from the signed value?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `DONConnectionManager`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: donId in user requests routed to a DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `donId in user requests routed to a DON` with padding, embedded NULs or non-UTF8 bytes.
- Invariant to test: encode/decode must round-trip exactly for every input
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: round-trip fuzz-style table test over the alignment helpers
