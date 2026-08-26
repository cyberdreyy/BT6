# Q1865: empty or absent signature accepted in codec.Codec

## Question
Does a request with an empty, zero or absent signature at the encode/decode boundary for gateway user requests and responses pass through `Codec` and receive an identity (zero address) that later checks treat as valid?

## Target
- File/function: [core/services/gateway/api/codec.go](core/services/gateway/api/codec.go) -> `Codec`
- Entrypoint: the encode/decode boundary for gateway user requests and responses
- Attacker controls: encoding variants of the same logical request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `encoding variants of the same logical request` without signature material.
- Invariant to test: missing signatures must be rejected before identity assignment
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test with empty/zero signatures
