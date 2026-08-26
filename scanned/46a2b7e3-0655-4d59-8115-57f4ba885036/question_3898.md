# Q3898: JSON parsing differential in wsconnection.Write

## Question
Do duplicate keys, unknown fields or type coercion in the body parsed by `Write` at an established gateway WebSocket connection let any internet client with an arbitrary externally-owned key sending signed gateway requests present one value to validation and another to execution?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `Write`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: frame contents and framing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `frame contents and framing` with duplicate/aliased keys.
- Invariant to test: decoding must reject duplicates/unknown fields and be used once
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test decoding hostile JSON twice and comparing
