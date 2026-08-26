# Q3581: sender field trusted over recovered signer in httpserver.handleHealthCheck

## Question
Does code reached from `handleHealthCheck` at the public gateway user HTTP endpoint (POST to the configured user path) use the self-declared sender field rather than the address recovered from the signature, letting any internet client with an arbitrary externally-owned key sending signed gateway requests impersonate another gateway user?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request body bytes and Content-Length (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `request body bytes and Content-Length` with a sender field naming a victim and a valid attacker signature.
- Invariant to test: the acting identity must be the recovered signer only
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: unit test asserting sender is overwritten by the recovered address
