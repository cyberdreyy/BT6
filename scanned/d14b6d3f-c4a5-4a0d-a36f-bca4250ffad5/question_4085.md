# Q4085: challenge reuse or predictability in httpserver.handleHealthCheck

## Question
Is the challenge produced/validated by `handleHealthCheck` at the public gateway user HTTP endpoint (POST to the configured user path) predictable, reusable or unbound to the connection, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay a captured handshake response?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request body bytes and Content-Length (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `request body bytes and Content-Length` captured from another handshake.
- Invariant to test: challenges must be random, single-use and connection-bound
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a handshake response
