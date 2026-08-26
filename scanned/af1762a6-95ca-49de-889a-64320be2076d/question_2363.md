# Q2363: JSON parsing differential in httpserver.NewHTTPServer

## Question
Do duplicate keys, unknown fields or type coercion in the body parsed by `NewHTTPServer` at the public gateway user HTTP endpoint (POST to the configured user path) let any internet client with an arbitrary externally-owned key sending signed gateway requests present one value to validation and another to execution?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `NewHTTPServer`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: source address and forwarding headers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `source address and forwarding headers` with duplicate/aliased keys.
- Invariant to test: decoding must reject duplicates/unknown fields and be used once
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test decoding hostile JSON twice and comparing
