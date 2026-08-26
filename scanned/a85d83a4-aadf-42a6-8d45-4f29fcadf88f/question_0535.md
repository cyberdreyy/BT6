# Q0535: JSON parsing differential in gateway.NewGatewayFromConfig

## Question
Do duplicate keys, unknown fields or type coercion in the body parsed by `NewGatewayFromConfig` at ProcessRequest on the public gateway user endpoint let any internet client with an arbitrary externally-owned key sending signed gateway requests present one value to validation and another to execution?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `NewGatewayFromConfig`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: method and donId routing fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `method and donId routing fields` with duplicate/aliased keys.
- Invariant to test: decoding must reject duplicates/unknown fields and be used once
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test decoding hostile JSON twice and comparing
