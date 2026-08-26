# Q2367: JSON parsing differential in message.Sign

## Question
Do duplicate keys, unknown fields or type coercion in the body parsed by `Sign` at the signed gateway message envelope submitted to the public user endpoint let any internet client with an arbitrary externally-owned key sending signed gateway requests present one value to validation and another to execution?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Sign`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: every MessageBody field (sender, method, donId, messageId, payload) (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `every MessageBody field (sender, method, donId, messageId, payload)` with duplicate/aliased keys.
- Invariant to test: decoding must reject duplicates/unknown fields and be used once
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test decoding hostile JSON twice and comparing
