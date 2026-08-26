# Q0614: legacy and JSON-RPC envelopes disagree in gateway.NewGatewayFromConfig

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit the same logical request in the alternate envelope form at ProcessRequest on the public gateway user endpoint so `NewGatewayFromConfig` applies weaker validation or a different identity?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `NewGatewayFromConfig`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: the message payload (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `message payload` in both envelope forms.
- Invariant to test: both envelope forms must converge on identical validation and identity
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across the two envelope paths
