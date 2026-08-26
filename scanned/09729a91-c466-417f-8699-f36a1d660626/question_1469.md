# Q1469: unauthenticated method reachable in httpserver.ensureLimiters

## Question
Is a gateway method routed by `ensureLimiters` at the public gateway user HTTP endpoint (POST to the configured user path) reachable without the authorization the handler assumes, letting any internet client with an arbitrary externally-owned key sending signed gateway requests invoke privileged capability paths?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `ensureLimiters`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: source address and forwarding headers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `source address and forwarding headers` for each advertised method without credentials.
- Invariant to test: every method must declare and enforce its own authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test invoking every method unauthenticated
