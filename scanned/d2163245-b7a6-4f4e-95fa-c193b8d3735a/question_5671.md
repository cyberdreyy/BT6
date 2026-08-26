# Q5671: initiator credential compared unsafely in orm.CreateBridgeType

## Question
Does the credential check in `CreateBridgeType` reached from bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs compare the presented secret non-constant-time or against a truncated hash, letting an authenticated node user holding only the 'edit' role (non-admin) recover or forge an accepted credential?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `CreateBridgeType`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed/truncated variants of `bridge name and URL`.
- Invariant to test: credential verification must be constant time over the full hashed secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing/table test over the authentication helper
