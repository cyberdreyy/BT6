# Q3852: metadata sync race grants access in handler.copiedResponses

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit at the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint during the metadata refresh handled by `copiedResponses` so authorization is evaluated against empty or stale metadata and defaults to allow?

## Target
- File/function: [core/services/gateway/handlers/vault/handler.go](core/services/gateway/handlers/vault/handler.go) -> `copiedResponses`
- Entrypoint: the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint
- Attacker controls: owner/namespace/secret identifier fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `owner/namespace/secret identifier fields` against the sync tick.
- Invariant to test: authorization must fail closed while metadata is unavailable or stale
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test submitting during a metadata gap and asserting rejection
