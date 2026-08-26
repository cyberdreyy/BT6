# Q5744: metadata sync race grants access in handler.armGraceDeadline

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit at HandleJSONRPCUserMessage on the confidential-relay gateway method during the metadata refresh handled by `armGraceDeadline` so authorization is evaluated against empty or stale metadata and defaults to allow?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `armGraceDeadline`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: requestID used to key the active request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `requestID used to key the active request` against the sync tick.
- Invariant to test: authorization must fail closed while metadata is unavailable or stale
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test submitting during a metadata gap and asserting rejection
