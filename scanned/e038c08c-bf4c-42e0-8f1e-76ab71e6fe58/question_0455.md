# Q0455: length/alignment helper mismatch in codec.Codec

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a string/byte field at the encode/decode boundary for gateway user requests and responses whose alignment or length handling in `Codec` makes the parsed value differ from the signed value?

## Target
- File/function: [core/services/gateway/api/codec.go](core/services/gateway/api/codec.go) -> `Codec`
- Entrypoint: the encode/decode boundary for gateway user requests and responses
- Attacker controls: encoding variants of the same logical request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `encoding variants of the same logical request` with padding, embedded NULs or non-UTF8 bytes.
- Invariant to test: encode/decode must round-trip exactly for every input
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: round-trip fuzz-style table test over the alignment helpers
