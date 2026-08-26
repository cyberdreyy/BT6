# Q0534: JSON parsing differential in codec.Codec

## Question
Do duplicate keys, unknown fields or type coercion in the body parsed by `Codec` at the encode/decode boundary for gateway user requests and responses let any internet client with an arbitrary externally-owned key sending signed gateway requests present one value to validation and another to execution?

## Target
- File/function: [core/services/gateway/api/codec.go](core/services/gateway/api/codec.go) -> `Codec`
- Entrypoint: the encode/decode boundary for gateway user requests and responses
- Attacker controls: the raw request bytes (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `raw request bytes` with duplicate/aliased keys.
- Invariant to test: decoding must reject duplicates/unknown fields and be used once
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test decoding hostile JSON twice and comparing
