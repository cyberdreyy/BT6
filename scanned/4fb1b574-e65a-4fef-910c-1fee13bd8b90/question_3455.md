# Q3455: empty or absent signature accepted in httpserver.NewHTTPServer

## Question
Does a request with an empty, zero or absent signature at the public gateway user HTTP endpoint (POST to the configured user path) pass through `NewHTTPServer` and receive an identity (zero address) that later checks treat as valid?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `NewHTTPServer`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request repetition rate (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `request repetition rate` without signature material.
- Invariant to test: missing signatures must be rejected before identity assignment
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test with empty/zero signatures
