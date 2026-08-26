# Q0684: deserialization accepts hostile fields in orm.NewORM

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit a payload at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs whose unmarshalling in `NewORM` sets fields the API does not expose (id, owner, token, created_at), taking over an existing record?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `NewORM`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `bridge name and URL` with extra JSON fields.
- Invariant to test: unmarshalling must reject unknown and server-owned fields
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test posting bodies with server-owned fields
