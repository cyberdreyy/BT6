# Q3959: legacy and JSON-RPC envelopes disagree in httpserver.handleHealthCheck

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit the same logical request in the alternate envelope form at the public gateway user HTTP endpoint (POST to the configured user path) so `handleHealthCheck` applies weaker validation or a different identity?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request repetition rate (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `request repetition rate` in both envelope forms.
- Invariant to test: both envelope forms must converge on identical validation and identity
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across the two envelope paths
