# Q1479: unauthenticated method reachable in utils.Uint32ToBytes

## Question
Is a gateway method routed by `Uint32ToBytes` at the encoding/signing helpers used on every gateway message before authorization reachable without the authorization the handler assumes, letting any internet client with an arbitrary externally-owned key sending signed gateway requests invoke privileged capability paths?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `Uint32ToBytes`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: strings and byte lengths that hit alignment/padding helpers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `strings and byte lengths that hit alignment/padding helpers` for each advertised method without credentials.
- Invariant to test: every method must declare and enforce its own authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test invoking every method unauthenticated
