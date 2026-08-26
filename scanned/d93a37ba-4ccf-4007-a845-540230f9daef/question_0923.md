# Q0923: origin allowlist bypass in httpserver.ensureLimiters

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests bypass the origin check in `ensureLimiters` at the public gateway user HTTP endpoint (POST to the configured user path) with case, suffix, port or null-origin tricks and drive the gateway from a browser context?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `ensureLimiters`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request repetition rate (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `request repetition rate` with crafted Origin values.
- Invariant to test: origin matching must be exact against the configured list
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over isAllowedOrigin with hostile origins
