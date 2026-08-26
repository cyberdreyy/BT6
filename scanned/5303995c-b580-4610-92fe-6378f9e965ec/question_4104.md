# Q4104: grace window abused to alter the bundle in handler.copiedResponses

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit during the grace window handled by `copiedResponses` at the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint so the bundle returned to the user includes or omits responses chosen by the attacker?

## Target
- File/function: [core/services/gateway/handlers/vault/handler.go](core/services/gateway/handlers/vault/handler.go) -> `copiedResponses`
- Entrypoint: the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint
- Attacker controls: owner/namespace/secret identifier fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `owner/namespace/secret identifier fields` against the grace deadline.
- Invariant to test: bundle composition must be determined by verified node responses only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test over bundle composition across grace timings
