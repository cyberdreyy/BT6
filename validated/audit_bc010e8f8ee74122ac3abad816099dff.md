### Title
View-role GraphQL user can read bridge `outgoingToken` secret via `Bridge` query - (File: core/web/resolver/query.go)

### Summary
The `Bridge` query resolver only calls `authenticateUser`, which grants access to any authenticated session regardless of role (including `UserRoleView`), and returns a `BridgeResolver` whose `OutgoingToken()` method returns the raw, unredacted outgoing token stored in the database. This lets the lowest-privileged role read a bridge's outgoing credential.

### Finding Description
`(r *Resolver) Bridge` in [1](#0-0)  gates access with `authenticateUser`, which only checks that a valid session exists and explicitly documents that "presence of user inherently provides 'view' access" [2](#0-1) . Compare this to `authenticateUserCanEdit`, used for `UpdateBridge`, which excludes `UserRoleView` and `UserRoleRun` [3](#0-2) .

Once authorized, the resolver loads the bridge via `FindBridge`, which executes `SELECT * FROM bridge_types WHERE name = $1`, populating the full `BridgeType` struct including `OutgoingToken` [4](#0-3) [5](#0-4) . The GraphQL schema exposes `outgoingToken: String!` directly on the `Bridge` type [6](#0-5) , and `BridgeResolver.OutgoingToken()` returns the value with no redaction or role check: `return r.bridge.OutgoingToken` [7](#0-6) .

The existing test suite confirms this is the current (unguarded) behavior: `Test_Bridge` exercises the query with only `authenticated: true` (no role differentiation) and asserts the raw `outgoingToken` value ("outgoingToken") is returned in the response [8](#0-7) . There is no test asserting that a view-role session is blocked or that the field is redacted for that role.

Unlike the incoming token (which is only ever returned once at bridge creation as a plaintext value paired with a hash stored server-side, per `IncomingTokenHash`/`Salt` in `BridgeType`), the `OutgoingToken` is stored and retrieved in plaintext on every `FindBridge` call and is not classified/access-controlled separately from non-sensitive bridge metadata (name, URL, confirmations). This is the actual bridge credential used to authenticate the Chainlink node when calling out to the external adapter, so its exposure to a view-only user is a genuine confidentiality violation of the intended role hierarchy (`view` < `run` < `edit` < `admin`).

### Impact Explanation
A user granted only `view` role — the lowest privilege tier intended for read-only dashboard/monitoring access — can retrieve the outgoing bridge token/credential for any bridge on the node via a single GraphQL query. This credential can be used to impersonate the node when calling the external adapter or, depending on how the external adapter uses the outgoing token, could allow further lateral abuse. This matches a "sensitive credential/secret disclosure to under-privileged role" impact class.

### Likelihood Explanation
Preconditions are minimal: any valid authenticated session with `UserRoleView` (the lowest privilege level assignable) is sufficient. No special conditions, timing, or race are required — a single POST to `/query` with `{ bridge(id:"name") { outgoingToken } }` is the entire exploit, fully repeatable for every bridge configured on the node.

### Recommendation
Change the `Bridge` and `Bridges` resolvers (or specifically the `OutgoingToken` resolver) to require at least `run`/`edit` role via `authenticateUserCanRun`/`authenticateUserCanEdit`, or redact `OutgoingToken` in the GraphQL response for `UserRoleView` sessions, consistent with how other privileged mutations (`UpdateBridge`) already restrict view-role access.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/resolver/bridge_test.go`):
1. Add a `gqlTestFramework` session fixture with `session.User.Role = sessions.UserRoleView`.
2. Run the existing `GetBridge` query (`bridge(id: "bridge1") { ... outgoingToken ... }`) authenticated as that view-role session against a mocked `bridgeORM.FindBridge` returning a bridge with `OutgoingToken: "secret-outgoing-token"`.
3. Assert current (vulnerable) behavior: the GraphQL response includes `"outgoingToken": "secret-outgoing-token"` with no error — demonstrating the view-role user obtained the secret.
4. After the fix, re-run the same test and assert either a `RoleNotPermittedError`/`UNAUTHORIZED` GraphQL error is returned, or `outgoingToken` is omitted/redacted in the response for the view-role session.

### Citations

**File:** core/web/resolver/query.go (L27-48)
```go
// Bridge retrieves a bridges by name.
func (r *Resolver) Bridge(ctx context.Context, args struct{ ID graphql.ID }) (*BridgePayloadResolver, error) {
	if err := authenticateUser(ctx); err != nil {
		return nil, err
	}

	name, err := bridges.ParseBridgeName(string(args.ID))
	if err != nil {
		return nil, err
	}

	bridge, err := r.App.BridgeORM().FindBridge(ctx, name)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return NewBridgePayload(bridge, err), nil
		}

		return nil, err
	}

	return NewBridgePayload(bridge, nil), nil
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

**File:** core/web/resolver/auth.go (L31-43)
```go
// Authenticates the user from the session cookie and asserts at least 'edit' role.
func authenticateUserCanEdit(ctx context.Context) error {
	session, ok := auth.GetGQLAuthenticatedSession(ctx)
	if !ok {
		return unauthorizedError{}
	}
	switch session.User.Role {
	case sessions.UserRoleView, sessions.UserRoleRun:
		return RoleNotPermittedError{session.User.Role}
	default:
	}
	return nil
}
```

**File:** core/bridges/orm.go (L56-63)
```go
// FindBridge looks up a Bridge by its Name.
// Returns sql.ErrNoRows if name not present
func (o *orm) FindBridge(ctx context.Context, name BridgeName) (bt BridgeType, err error) {
	stmt := "SELECT * FROM bridge_types WHERE name = $1"
	err = o.ds.GetContext(ctx, &bt, stmt, name.String())

	return
}
```

**File:** core/bridges/bridge_type.go (L55-68)
```go
// BridgeType is used for external adapters and has fields for
// the name of the adapter and its URL.
type BridgeType struct {
	Name                   BridgeName
	URL                    models.WebURL
	Confirmations          uint32
	IncomingTokenHash      string
	Salt                   string
	OutgoingToken          string
	MinimumContractPayment *assets.Link
	CreatedAt              time.Time
	UpdatedAt              time.Time
	UseConnectionManager   bool `json:"useConnectionManager"`
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

**File:** core/web/resolver/bridge.go (L52-55)
```go
// OutgoingToken resolves the bridge's outgoing token.
func (r *BridgeResolver) OutgoingToken() string {
	return r.bridge.OutgoingToken
}
```

**File:** core/web/resolver/bridge_test.go (L86-141)
```go
func Test_Bridge(t *testing.T) {
	t.Parallel()

	var (
		query = `
			query GetBridge{
				bridge(id: "bridge1") {
					... on Bridge {
						id
						name
						url
						confirmations
						outgoingToken
						minimumContractPayment
						createdAt
					}
					... on NotFoundError {
						message
						code
					}
				}
			}`

		name = bridges.BridgeName("bridge1")
	)
	bridgeURL, err := url.Parse("https://external.adapter")
	require.NoError(t, err)

	testCases := []GQLTestCase{
		unauthorizedTestCase(GQLTestCase{query: query}, "bridge"),
		{
			name:          "success",
			authenticated: true,
			before: func(ctx context.Context, f *gqlTestFramework) {
				f.App.On("BridgeORM").Return(f.Mocks.bridgeORM)
				f.Mocks.bridgeORM.On("FindBridge", mock.Anything, name).Return(bridges.BridgeType{
					Name:                   name,
					URL:                    models.WebURL(*bridgeURL),
					Confirmations:          uint32(1),
					OutgoingToken:          "outgoingToken",
					MinimumContractPayment: assets.NewLinkFromJuels(1),
					CreatedAt:              f.Timestamp(),
				}, nil)
			},
			query: query,
			result: `{
				"bridge": {
					"id": "bridge1",
					"name": "bridge1",
					"url": "https://external.adapter",
					"confirmations": 1,
					"outgoingToken": "outgoingToken",
					"minimumContractPayment": "1",
					"createdAt": "2021-01-01T00:00:00Z"
				}
			}`,
```
