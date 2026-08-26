# Q0459: length/alignment helper mismatch in utils.Uint32ToBytes

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a string/byte field at the encoding/signing helpers used on every gateway message before authorization whose alignment or length handling in `Uint32ToBytes` makes the parsed value differ from the signed value?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `Uint32ToBytes`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: nested payload structures passed to Flatten (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `nested payload structures passed to Flatten` with padding, embedded NULs or non-UTF8 bytes.
- Invariant to test: encode/decode must round-trip exactly for every input
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: round-trip fuzz-style table test over the alignment helpers
