# Q2565: challenge reuse or predictability in gateway.setupFromNewConfig

## Question
Is the challenge produced/validated by `setupFromNewConfig` at ProcessRequest on the public gateway user endpoint predictable, reusable or unbound to the connection, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay a captured handshake response?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `setupFromNewConfig`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: method and donId routing fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `method and donId routing fields` captured from another handshake.
- Invariant to test: challenges must be random, single-use and connection-bound
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a handshake response
