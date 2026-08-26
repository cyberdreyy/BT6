# Q4817: hex/address normalization differences in handshake.PackChallenge

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present an address or identifier at the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange) in a casing/encoding variant that `PackChallenge` treats as distinct from the allowlisted form, bypassing an authorization or quota keyed on the other form?

## Target
- File/function: [core/services/gateway/network/handshake.go](core/services/gateway/network/handshake.go) -> `PackChallenge`
- Entrypoint: the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange)
- Attacker controls: the challenge bytes echoed back (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `challenge bytes echoed back` in mixed case, checksummed, zero-padded or 0x-less form.
- Invariant to test: identifiers must be canonicalized once before any authorization or accounting decision
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over normalization for all encodings of one identity
