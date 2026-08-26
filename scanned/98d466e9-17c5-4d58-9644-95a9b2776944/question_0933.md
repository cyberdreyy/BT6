# Q0933: origin allowlist bypass in utils.Uint32ToBytes

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests bypass the origin check in `Uint32ToBytes` at the encoding/signing helpers used on every gateway message before authorization with case, suffix, port or null-origin tricks and drive the gateway from a browser context?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `Uint32ToBytes`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: nested payload structures passed to Flatten (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `nested payload structures passed to Flatten` with crafted Origin values.
- Invariant to test: origin matching must be exact against the configured list
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over isAllowedOrigin with hostile origins
