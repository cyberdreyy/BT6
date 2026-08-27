### Title
GraphQL `Bridge.outgoingToken` field leaks bridge's confidential outgoing auth token to any authenticated (view-role) session - ([File: core/web/resolver/bridge.go])

### Summary
The `Bridge` GraphQL type declares `outgoingToken: String!` in `core/web/schema/type/bridge.graphql`, and `BridgeResolver.OutgoingToken()` returns the plaintext `bridges.BridgeType.OutgoingToken` value with no redaction. Any session (including view-role) that can call `Resolver.Bridge`/`Resolver.Bridges` in `core/web/resolver/query.go` and knows/guesses a bridge name can retrieve this secret via a normal `bridge(id:"name"){ outgoingToken }` query.

### Finding Description
`BridgeType` (`core/bridges/bridge_type.go` lines 57-68) stores `OutgoingToken` in plaintext (unlike `IncomingTokenHash`, which is only ever stored hashed and is never exposed through a GraphQL resolver method or the schema — `bridge.graphql` has no `incomingTokenHash` field and `bridge.go` has no such resolver method, so that half of the reported concern is not exploitable).

However, `BridgeResolver.OutgoingToken()` in `core/web/resolver/bridge.go` (lines 52-55):
```go
func (r *BridgeResolver) OutgoingToken() string {
	return r.bridge.OutgoingToken
}
```
directly returns the raw `OutgoingToken` secret, and this resolver method is wired to the schema field `outgoingToken: String!` declared in `core/web/schema/type/bridge.graphql` (line 6) on the `Bridge` type. `Resolver.Bridge`/`Resolver.Bridges` (`core/web/resolver/query.go`) resolve this type via `NewBridgePayload`/`NewBridgesPayload` → `NewBridge`/`NewBridges` (`core/web/resolver/bridge.go` lines 16-27, 78-91), which populate `BridgeResolver.bridge` with the full `bridges.BridgeType`, secret field included.

There is no field-level redaction, no separate presenter distinguishing "public" vs "secret" fields for reads (contrast this with `CreateBridgeSuccessResolver.IncomingToken()`, which is intentionally the only place a fresh incoming token is meant to be surfaced, at creation time only). GraphQL queries in this codebase are gated by session authentication but query-level role checks (e.g., `auth.RequireRole`) are typically only enforced on mutations (create/update/delete bridge); read-only queries such as `bridge`/`bridges` are reachable by any authenticated session including `view` role. As a result, a view-role user — who should only have read access to non-sensitive resources — can retrieve the bridge's live outgoing token used by the node to authenticate itself against the external adapter.

### Impact Explanation
This matches the "sensitive secret/credential disclosure" bounty class: the outgoing token is a credential the Chainlink node uses to authenticate to external bridge adapters. Its disclosure via a view-role GraphQL query allows an unprivileged/read-only user to impersonate the node when calling the external adapter that trusts that token, potentially manipulating job results or exfiltrating further adapter-side secrets/behavior.

### Likelihood Explanation
Feasibility is high: the attacker only needs a valid session with `view` role (the lowest privileged authenticated role) and knowledge/guessability of a bridge name (bridge names are often deducible from job specs, external adapter naming conventions, or already-visible via other read endpoints/job DAG task names). The exploit is a single GraphQL query, fully repeatable, no timing or race conditions required.

### Recommendation
Remove the `outgoingToken` field from the publicly queryable `Bridge` GraphQL type (`core/web/schema/type/bridge.graphql`) and the corresponding `BridgeResolver.OutgoingToken()` resolver in `core/web/resolver/bridge.go`, or restrict access to admin/edit role only and redact/mask the value for read queries the same way `IncomingTokenHash` is never exposed. If some consumers legitimately need to view/rotate the outgoing token, expose it only through a dedicated, admin-role-gated mutation/query, not as a default field of the general-purpose `Bridge` read type.

### Proof of Concept
1. Go resolver test (extend `core/web/resolver/bridge_test.go`):
   - Seed a `bridges.BridgeType` with a known `OutgoingToken` value via the mocked bridge ORM.
   - Execute the GraphQL query `{ bridge(id:"knownBridgeName") { name outgoingToken } }` using a test client authenticated with `view` role session (see how other tests in `core/web/resolver` set up role-scoped sessions, e.g. via `authenticatedRole` or similar helper).
   - Assert the HTTP/GraphQL response is NOT `null`/error for `outgoingToken` and instead returns the plaintext seeded token — demonstrating the leak.
   - Expected (fixed) behavior: querying `outgoingToken` as `view` role should either be schema-rejected (field removed) or return an authorization error, and the field should never carry the raw secret in any role's response.
2. Additionally assert that `incomingTokenHash` is not a resolvable field in the schema (regression guard) to confirm that half of the original concern remains non-issue.