# Q5060: callback delivered to the wrong caller in workflow_metadata_handler.syncMetadata

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive the callback resolved by `syncMetadata` at the workflow metadata/authorization lookup consulted for every user trigger request for another user's request through duplicate/late/out-of-order responses?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `syncMetadata`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: authorization key material presented (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `authorization key material presented` timed against a victim's in-flight request.
- Invariant to test: each callback must fire once, to the originating connection only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test asserting single-delivery per originating request
