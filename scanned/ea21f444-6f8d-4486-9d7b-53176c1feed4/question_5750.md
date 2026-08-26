# Q5750: metadata sync race grants access in message_util.ValidatedResponseFromMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit at validated conversion between gateway messages, requests and responses during the metadata refresh handled by `ValidatedResponseFromMessage` so authorization is evaluated against empty or stale metadata and defaults to allow?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedResponseFromMessage`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: response fields echoed from the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `response fields echoed from the request` against the sync tick.
- Invariant to test: authorization must fail closed while metadata is unavailable or stale
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test submitting during a metadata gap and asserting rejection
