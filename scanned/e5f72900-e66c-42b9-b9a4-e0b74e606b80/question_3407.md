# Q3407: secret ownership check on the wrong field in shard_endpoints.allMembers

## Question
Does the ownership check for a vault secret in `allMembers` at shard selection for a user trigger request routed to a sharded DON use a request field rather than the recovered signer, letting any internet client with an arbitrary externally-owned key sending signed gateway requests read or overwrite another owner's secret?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `allMembers`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: donId/shard fields in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `donId/shard fields in the request` naming the victim's owner/namespace.
- Invariant to test: secret access must be authorized against the recovered signer only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test reading a foreign owner's secret
