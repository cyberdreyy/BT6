### Title
Bridge `outgoingToken` secret exposed to any authenticated user via GraphQL, including view-only accounts - ([File: core/web/resolver/bridge.go])

### Summary
The `Bridges`/`Bridge` GraphQL queries only call `authenticateUser(ctx)` before returning bridge data, and the `BridgeResolver.OutgoingToken()` field resolver unconditionally returns `r.bridge.OutgoingToken` with no further per-field or per-role check. Any authenticated session — including a view-only role — can read the bridge's outgoing token, which is a secret used to authenticate the node's outbound webhook calls to external adapters.

### Finding Description
`core/web/resolver/query.go` implements `Resolver.Bridge` and `Resolver.Bridges`, both of which gate access solely with: [1](#0-0) [2](#0-1) 

`authenticateUser` only validates that a session exists — it does not check the user's role (e.g. admin/edit vs. view-only). Once the query succeeds, the returned `BridgesPayloadResolver`/`BridgePayloadResolver` wrap `bridges.BridgeType` values in `BridgeResolver` objects via `NewBridge`/`NewBridges`: [3](#0-2) 

The GraphQL schema exposes `outgoingToken: String!` directly on the `Bridge` type: [4](#0-3) 

And the field resolver returns the raw secret with no additional authorization check: [5](#0-4) 

An attacker holding a valid view-only session (a legitimate but low-privilege authenticated principal) can issue `query { bridges { results { name outgoingToken } } }` and receive the outgoing token for every configured bridge, without any edit/admin-level authorization check being performed anywhere in the call chain.

### Impact Explanation
`OutgoingToken` is a bridge secret used to authenticate the Chainlink node's outbound calls to external adapters (bridges). Disclosure of this token to a low-privileged, view-only authenticated principal lets that principal impersonate the node when calling the external adapter endpoint, potentially manipulating job run data/results for jobs that rely on that bridge. This falls under "secret/credential disclosure" and "role/authorization bypass" impact classes — an authorization boundary (view vs. edit/admin) that should gate secret material is missing at the per-field level.

### Likelihood Explanation
The only precondition is possessing any valid, low-privileged (view-only) authenticated session/API token for the node's GraphQL endpoint — no special permissions, chain access, or additional exploitation steps are required. The query is trivial, deterministic, and repeatable (`query bridges{results{name outgoingToken}}`), making this a low-effort, reliably reproducible information-disclosure path for any user granted read-only node access.

### Recommendation
Add a per-field or per-query role check (e.g. `authenticateUserCanEdit`/`authenticateUserIsAdmin`) before exposing `outgoingToken` (and any other secret-bearing bridge fields) to GraphQL clients, or omit `outgoingToken` from the `Bridge` type entirely and only return it from bridge creation/update mutation payloads (as is already done for `incomingToken` in `CreateBridgeSuccessResolver`). At minimum, gate `Resolver.Bridges`/`Resolver.Bridge` (or the `OutgoingToken()` field resolver specifically) behind an edit/admin-level role check consistent with other secret-exposing resolvers in the codebase.

### Proof of Concept
1. Add a Go resolver test in `core/web/resolver/bridge_test.go` (or a schema-level test) that:
   - Creates a test app/session with a view-only role user (`authenticateUserCanEdit`-failing role, e.g. `sessions.UserRoleView`).
   - Seeds a bridge with a known `OutgoingToken` value.
   - Executes `query { bridges { results { name outgoingToken } } }` through the GraphQL test harness (`clhttptest`/`gqlgen` test client) authenticated as the view-only user.
   - Asserts the query currently succeeds and returns the plaintext `outgoingToken` (demonstrating the vulnerability), and that after the fix, the query would either omit the field or return an authorization error for non-edit/admin roles.
2. A companion schema-enumeration test can iterate all `Bridge` fields and assert that any field named `*Token`/`*Secret` is only resolvable when the resolver performs an edit/admin role check rather than bare `authenticateUser`.

### Citations

**File:** core/web/resolver/query.go (L28-31)
```go
func (r *Resolver) Bridge(ctx context.Context, args struct{ ID graphql.ID }) (*BridgePayloadResolver, error) {
	if err := authenticateUser(ctx); err != nil {
		return nil, err
	}
```

**File:** core/web/resolver/query.go (L55-57)
```go
	if err := authenticateUser(ctx); err != nil {
		return nil, err
	}
```

**File:** core/web/resolver/bridge.go (L16-27)
```go
func NewBridge(bridge bridges.BridgeType) *BridgeResolver {
	return &BridgeResolver{bridge: bridge}
}

func NewBridges(bridges []bridges.BridgeType) []*BridgeResolver {
	resolvers := make([]*BridgeResolver, 0, len(bridges))
	for _, b := range bridges {
		resolvers = append(resolvers, NewBridge(b))
	}

	return resolvers
}
```

**File:** core/web/resolver/bridge.go (L52-55)
```go
// OutgoingToken resolves the bridge's outgoing token.
func (r *BridgeResolver) OutgoingToken() string {
	return r.bridge.OutgoingToken
}
```

**File:** core/web/schema/type/bridge.graphql (L1-10)
```text
type Bridge {
    id: ID!
    name: String!
    url: String!
    confirmations: Int!
    outgoingToken: String!
    minimumContractPayment: String!
    useConnectionManager: Boolean!
    createdAt: Time!
}
```
