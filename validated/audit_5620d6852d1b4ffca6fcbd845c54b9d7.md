### Title
View-role GraphQL user can bulk-enumerate all bridges' `OutgoingToken` secrets via paginated `Bridges` query - ([File: core/web/resolver/query.go], [File: core/web/resolver/bridge.go])

### Summary
`Resolver.Bridges` only calls `authenticateUser(ctx)` (any valid session, no role check) before returning every configured bridge's fields, and `BridgeResolver.OutgoingToken()` unconditionally serializes the bridge's `OutgoingToken` to the caller. Combined with pagination (`offset`/`limit`), a low-privilege view-role session can enumerate the `OutgoingToken` for every bridge in a single authenticated session.

### Finding Description
`Resolver.Bridges` (core/web/resolver/query.go:51-68) gates access with `authenticateUser(ctx)` only [1](#0-0) , which does not perform any role-based authorization (unlike admin/edit-gated mutations elsewhere in the resolver package). The resulting `BridgesPayloadResolver.Results()` returns `BridgeResolver` instances for every bridge in the page [2](#0-1) , and `BridgeResolver.OutgoingToken()` returns the raw `bridge.OutgoingToken` value with no redaction [3](#0-2) . The GraphQL schema explicitly declares `outgoingToken: String!` as a public field on `Bridge` [4](#0-3) . The equivalent REST presenter (`core/web/presenters/bridges.go`) has the same unconditional behavior, serializing `OutgoingToken` without `omitempty` [5](#0-4) , confirming this is a systemic pattern rather than a GraphQL-only oversight. Because `Bridges` supports `offset`/`limit` pagination, an attacker holding only a low-privilege ("view") authenticated session can page through the full bridge list and collect every `OutgoingToken` in the node — tokens that are meant to authenticate the node to external adapters, effectively a bearer credential for those adapters.

### Impact Explanation
This is a secret/credential disclosure: a low-privileged authenticated user (view role) can extract the outgoing bearer tokens for all configured external-adapter bridges in one session, enabling impersonation of the node when calling those adapters or reuse of the token to invoke privileged adapter functionality. This escalates from a single-bridge leak to full compromise of every external-adapter integration configured on the node, matching a "sensitive data exposure / secret disclosure" bounty class.

### Likelihood Explanation
The only precondition is possessing a valid low-privilege ("view" role) node session/API token — no admin, edit, or run privileges are required, and no additional gate exists inside `Resolver.Bridges` beyond generic authentication. The GraphQL query is trivially repeatable and supports built-in pagination (`offset`, `limit`) for full enumeration, making this fully feasible and repeatable by any authenticated low-privilege user.

### Recommendation
Either (a) restrict the `Bridges`/`Bridge` GraphQL queries and the equivalent REST controller to admin/edit roles, or (b) redact `OutgoingToken` (and `IncomingToken`) from the `Bridge`/`BridgeResource` responses for list/read operations, only exposing them at creation time (as already done for `IncomingToken` in `CreateBridgeSuccessResolver`), and add a role check (e.g., `authorizeAdmin`/`authorizeEdit`) before returning secret fields to view-role sessions.

### Proof of Concept
Go integration test in `core/web/resolver/bridge_test.go` style:
1. Seed multiple `bridges.BridgeType` records with distinct, easily-identifiable `OutgoingToken` values via `BridgeORM().CreateBridgeType`.
2. Construct a GraphQL test client authenticated with a session/user assigned only the "view" role (lowest privilege, matching existing `resolver_test.go` helpers for role-based auth).
3. Execute `query { bridges(offset:0, limit:1000) { results { name outgoingToken } metadata { total } } }`.
4. Assert the response succeeds (no authorization error) and that `results[].outgoingToken` contains the exact seeded secret values for every bridge, proving bulk disclosure to a view-role principal.
5. (Regression variant) Assert failure of the invariant proposed in the prompt — i.e., show that `BridgesPayloadResolver`/`BridgeResolver` DOES serialize `outgoingToken`, contradicting the expected "never serializes" behavior, which is the root cause of the disclosure.

### Citations

**File:** core/web/resolver/query.go (L50-67)
```go
// Bridges retrieves a paginated list of bridges.
func (r *Resolver) Bridges(ctx context.Context, args struct {
	Offset *int32
	Limit  *int32
}) (*BridgesPayloadResolver, error) {
	if err := authenticateUser(ctx); err != nil {
		return nil, err
	}

	offset := pageOffset(args.Offset)
	limit := pageLimit(args.Limit)

	brdgs, count, err := r.App.BridgeORM().BridgeTypes(ctx, offset, limit)
	if err != nil {
		return nil, err
	}

	return NewBridgesPayload(brdgs, safeInt32(count)), nil
```

**File:** core/web/resolver/bridge.go (L52-55)
```go
// OutgoingToken resolves the bridge's outgoing token.
func (r *BridgeResolver) OutgoingToken() string {
	return r.bridge.OutgoingToken
}
```

**File:** core/web/resolver/bridge.go (L93-109)
```go
// BridgesPayloadResolver resolves a page of bridges
type BridgesPayloadResolver struct {
	bridges []bridges.BridgeType
	total   int32
}

func NewBridgesPayload(bridges []bridges.BridgeType, total int32) *BridgesPayloadResolver {
	return &BridgesPayloadResolver{
		bridges: bridges,
		total:   total,
	}
}

// Results returns the bridges.
func (r *BridgesPayloadResolver) Results() []*BridgeResolver {
	return NewBridges(r.bridges)
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

**File:** core/web/presenters/bridges.go (L10-22)
```go
// BridgeResource represents a Bridge JSONAPI resource.
type BridgeResource struct {
	JAID
	Name          string `json:"name"`
	URL           string `json:"url"`
	Confirmations uint32 `json:"confirmations"`
	// The IncomingToken is only provided when creating a Bridge
	IncomingToken          string       `json:"incomingToken,omitempty"`
	OutgoingToken          string       `json:"outgoingToken"`
	MinimumContractPayment *assets.Link `json:"minimumContractPayment"`
	UseConnectionManager   bool         `json:"useConnectionManager"`
	CreatedAt              time.Time    `json:"createdAt"`
}
```
