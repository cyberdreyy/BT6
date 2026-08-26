# Q0930: origin allowlist bypass in gateway.NewGatewayFromConfig

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests bypass the origin check in `NewGatewayFromConfig` at ProcessRequest on the public gateway user endpoint with case, suffix, port or null-origin tricks and drive the gateway from a browser context?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `NewGatewayFromConfig`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: request repetition and concurrency (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `request repetition and concurrency` with crafted Origin values.
- Invariant to test: origin matching must be exact against the configured list
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over isAllowedOrigin with hostile origins
