# Q3726: per-owner quota not enforced in handler.copiedResponses

## Question
Does `copiedResponses` at the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint enforce rate/quota per authenticated owner, or can any internet client with an arbitrary externally-owned key sending signed gateway requests rotate identifiers to obtain unlimited DON execution charged elsewhere?

## Target
- File/function: [core/services/gateway/handlers/vault/handler.go](core/services/gateway/handlers/vault/handler.go) -> `copiedResponses`
- Entrypoint: the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint
- Attacker controls: signature over the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `signature over the request` across submissions.
- Invariant to test: quotas must key on the verified owner and be enforced before dispatch
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating identifiers and asserting the quota still applies
