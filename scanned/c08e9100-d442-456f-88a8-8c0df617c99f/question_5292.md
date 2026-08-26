# Q5292: request id mismatch tolerated in handler.NewHandler

## Question
Does `NewHandler` at the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint tolerate a mismatch between the id inside a signed payload and the id of the request being answered, letting any internet client with an arbitrary externally-owned key sending signed gateway requests splice a response from another request?

## Target
- File/function: [core/services/gateway/handlers/vault/handler.go](core/services/gateway/handlers/vault/handler.go) -> `NewHandler`
- Entrypoint: the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint
- Attacker controls: owner/namespace/secret identifier fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `owner/namespace/secret identifier fields` whose ids differ.
- Invariant to test: the signed id must equal the served request id
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting mismatched ids are rejected
