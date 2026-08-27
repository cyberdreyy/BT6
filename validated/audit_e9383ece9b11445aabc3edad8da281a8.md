### Title
Bridge `OutgoingToken` secret exposed via GraphQL `Bridge`/`Bridges` read queries to view-role users - ([File: core/web/resolver/bridge.go])

### Summary
The `Bridge` and `Bridges` query resolvers only require `authenticateUser` (minimum "view" role) and return a `BridgeResolver` whose `OutgoingToken()` field directly serializes the bridge's outgoing authentication token, which is credential material used by the node to authenticate itself against the external adapter. Any authenticated user with only the "view" role can read this secret by querying `bridge(id: ...) { outgoingToken }` or `bridges { results { outgoingToken } }`.

### Finding Description
`core/web/resolver/query.go`'s `Bridge` (lines 28-48) and `Bridges` (lines 51-68) resolvers call only `authenticateUser(ctx)` [1](#0-0) [2](#0-1) , which per `authenticateUser`'s own comment merely confirms "presence of user inherently provides 'view' access" — the lowest privilege role (`sessions.UserRoleView`), with no role-based restriction like `authenticateUserCanEdit`/`authenticateUserIsAdmin` applied.

Both resolvers return a `BridgeResolver` built from the full `bridges.BridgeType` domain object via `NewBridge`/`NewBridges` [3](#0-2) . `BridgeResolver` exposes an `OutgoingToken()` GraphQL field that returns the raw stored token with no redaction: [4](#0-3) 

This differs from the mutation path, where `CreateBridge` deliberately returns the plaintext token only once at creation time via `IncomingToken()` on `CreateBridgeSuccessResolver` [5](#0-4)  — implying token material is meant to be revealed only transiently, not on every subsequent read. The `Bridge`/`Bridges` read queries, however, unconditionally re-serialize `OutgoingToken` on every fetch to any "view"-role session, with no presenter-level redaction analogous to what REST/CLI presenters may apply.

### Impact Explanation
`OutgoingToken` is credential material the Chainlink node sends to authenticate against an external bridge/adapter endpoint. Disclosure lets a low-privilege "view" user impersonate the node to the external adapter or replay the token to access/manipulate bridge-protected resources, matching a credential-disclosure impact class. It is a scoped node-secret leak rather than direct private-key theft, so while serious it is narrower than "node blockchain private keys" disclosure.

### Likelihood Explanation
Exploitation requires only an authenticated GraphQL session with the default/lowest "view" role — no operator/admin/host access needed. The query is a single, unauthenticated-in-privilege GraphQL call (`bridge(id) { outgoingToken }` or `bridges { results { outgoingToken } }`), fully repeatable and requiring no special conditions once any valid view-role session exists.

### Recommendation
Redact `OutgoingToken` from the `Bridge` read-query GraphQL type (return only a masked/last-4 indicator, or omit entirely), and/or require at least `authenticateUserIsAdmin`/`authenticateUserCanEdit` for any query path that can resolve `outgoingToken`. Add an explicit schema-level test asserting the field is unreachable (or requires elevated role) from `Query`.

### Proof of Concept
1. Handler/GraphQL integration test: create a session user with `sessions.UserRoleView` only.
2. Seed a bridge with a known `OutgoingToken` value via `BridgeORM().CreateBridgeType`.
3. Execute GraphQL query `{ bridge(id: "<name>") { ... on Bridge { outgoingToken } } }` and separately `{ bridges { results { outgoingToken } } }` using the view-role session.
4. Assert the response contains the known `OutgoingToken` value, proving a view-role user can read node bridge secret material — expected/fixed behavior: query should fail with `RoleNotPermittedError` or the field should be redacted/omitted.

Note: I was only able to confirm this specific token-exposure path for `Bridge`/`Bridges`; other read resolvers referenced in the question (keys, config, nodes, features) were reviewed for structure but not exhaustively verified field-by-field for equivalent secret leakage — those resolvers (`ETHKeys`, `CSAKeys`, `VRFKeys`, `OCRKeyBundles`, etc.) appear to expose only public key identifiers based on the code inspected, but a full confirmation would require checking each corresponding resolver's field methods individually.

### Citations

**File:** core/web/resolver/query.go (L28-31)
```go
func (r *Resolver) Bridge(ctx context.Context, args struct{ ID graphql.ID }) (*BridgePayloadResolver, error) {
	if err := authenticateUser(ctx); err != nil {
		return nil, err
	}
```

**File:** core/web/resolver/auth.go (L11-17)
```go
// Authenticates the user from the session cookie, presence of user inherently provides 'view' access.
func authenticateUser(ctx context.Context) error {
	if _, ok := auth.GetGQLAuthenticatedSession(ctx); !ok {
		return unauthorizedError{}
	}
	return nil
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

**File:** core/web/resolver/bridge.go (L150-153)
```go
// Token resolves the bridge's incoming token.
func (r *CreateBridgeSuccessResolver) IncomingToken() string {
	return r.incomingToken
}
```
