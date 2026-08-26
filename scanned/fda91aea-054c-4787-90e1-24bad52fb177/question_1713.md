# Q1713: hex/address normalization differences in utils.Uint32ToBytes

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present an address or identifier at the encoding/signing helpers used on every gateway message before authorization in a casing/encoding variant that `Uint32ToBytes` treats as distinct from the allowlisted form, bypassing an authorization or quota keyed on the other form?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `Uint32ToBytes`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: strings and byte lengths that hit alignment/padding helpers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `strings and byte lengths that hit alignment/padding helpers` in mixed case, checksummed, zero-padded or 0x-less form.
- Invariant to test: identifiers must be canonicalized once before any authorization or accounting decision
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over normalization for all encodings of one identity
