# Q1576: external-initiator credential over-scoped in gql.AuthenticateGQL

## Question
Can an authenticated node user holding only the 'view' role use an external-initiator credential accepted by `AuthenticateGQL` on POST /query (GraphQL) guarded by AuthenticateGQL to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the GraphQL document (query/mutation/alias/fragment) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `GraphQL document (query/mutation/alias/fragment)` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
