### Title
Bridge outgoingToken (outbound auth credential) disclosed to any authenticated view-role user via GET /v2/bridge_types - ([File: core/web/bridge_types_controller.go])

### Summary
The `GET /v2/bridge_types` route is registered with only `paginatedRequest(bt.Index)` and no `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper, meaning any authenticated user (including view-role) can call it. `BridgeTypesController.Index` builds each response entry via `presenters.NewBridgeResource`, which copies `bridges.BridgeType.OutgoingToken` into `BridgeResource.OutgoingToken`, a field tagged `json:"outgoingToken"` (no `omitempty`), so it is always serialized and returned to the caller.

### Finding Description
- Route registration: `authv2.GET("/bridge_types", paginatedRequest(bt.Index))` at [1](#0-0)  only requires generic authentication (session or token) via the `authv2` group at [2](#0-1) , unlike the `Create`/`Update`/`Destroy` bridge routes which are wrapped with `auth.RequiresEditRole`. There is no role check for `Index`.
- `BridgeTypesController.Index` fetches all bridges from the DB and maps each to a `presenters.BridgeResource` without any redaction: [3](#0-2) .
- `presenters.NewBridgeResource` copies `b.OutgoingToken` directly into the resource's `OutgoingToken` field: [4](#0-3) .
- The `BridgeResource.OutgoingToken` field is declared without `omitempty` (unlike `IncomingToken`, which is explicitly commented as "only provided when creating a Bridge" and has `omitempty`): [5](#0-4) .
- `OutgoingToken` is a persisted plaintext secret (stored as `outgoing_token` in `bridge_types` table, set at creation time as `utils.NewSecret(24)`) that is sent by the node as the bearer/auth token when calling out to the external adapter: [6](#0-5) [7](#0-6) .
- Unlike `IncomingTokenHash`/`Salt` (used to verify inbound requests, hashed, and never exposed via `BridgeResource`), `OutgoingToken` is the live plaintext outbound credential the node presents to the bridge adapter, and it is unconditionally serialized in the list response.
- No middleware, presenter redaction, or role check filters this field out for lower-privileged roles calling the Index endpoint, so a view-role authenticated user can retrieve the credential for every bridge in the node.

### Impact Explanation
This is a credential/secret disclosure vulnerability: a view-role authenticated user (the lowest privileged role that can log in) can enumerate all configured bridges and obtain each bridge's `outgoingToken`, which is used as the node's outbound authentication credential to the corresponding external adapter. An attacker with only view privileges (no edit/admin rights) can use this token to impersonate the node when calling the external adapter directly, potentially manipulating bridge behavior, exhausting adapter quotas, or accessing adapter-side functionality gated by that token — exceeding what a view role should be permitted to do. This matches the "sensitive information disclosure — key/secret exposure across roles" bounty class.

### Likelihood Explanation
Fully deterministic and requires no special conditions beyond having any valid authenticated session/API token with the view role (the lowest role) and at least one bridge existing on the node (which is a common baseline). The exploit is a single unauthenticated-in-privilege-sense GET request to `/v2/bridge_types`, fully repeatable, and returns the secret in cleartext JSON every time.

### Recommendation
Add `omitempty` is not sufficient (token is non-empty by design); instead, exclude `OutgoingToken` from the `Index`/list presenter entirely (or restrict via role in the controller), mirroring how `IncomingToken` is only populated in the `Create` response. Concretely: remove `OutgoingToken: b.OutgoingToken` from `presenters.NewBridgeResource` used by `Index`/`Show`, and only return it (if at all) to admin/edit-role callers, or better, never return it after creation and instead support a token-rotation endpoint if the operator needs to re-obtain it.

### Proof of Concept
Go handler-level integration test plan (extends existing `TestBridgeTypesController_Index` in `core/web/bridge_types_controller_test.go`):
1. Start `cltest.NewApplication`, create a bridge type as admin via `orm.CreateBridgeType` (or POST `/v2/bridge_types` as admin), capturing the returned/stored `bt.OutgoingToken`.
2. Create an HTTP client authenticated with a session/token belonging to a user with `sessions.UserRoleView` (view-only) role instead of admin/edit.
3. Issue `client.Get("/v2/bridge_types")` as the view-role client.
4. Assert HTTP 200, parse the JSON:API response into `[]presenters.BridgeResource`.
5. Assert `resources[0].OutgoingToken != ""` and equals the previously stored `bt.OutgoingToken`, proving the outbound secret is disclosed to a view-role principal that has no edit/admin rights on bridges.

### Citations

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L268-273)
```go
		bt := BridgeTypesController{app}
		authv2.GET("/bridge_types", paginatedRequest(bt.Index))
		authv2.POST("/bridge_types", auth.RequiresEditRole(bt.Create))
		authv2.GET("/bridge_types/:BridgeName", bt.Show)
		authv2.PATCH("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Update))
		authv2.DELETE("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Destroy))
```

**File:** core/web/bridge_types_controller.go (L111-122)
```go
// Index lists Bridges, one page at a time.
func (btc *BridgeTypesController) Index(c *gin.Context, size, page, offset int) {
	ctx := c.Request.Context()
	bridges, count, err := btc.App.BridgeORM().BridgeTypes(ctx, offset, size)

	resources := make([]presenters.BridgeResource, 0, len(bridges))
	for _, bridge := range bridges {
		resources = append(resources, *presenters.NewBridgeResource(bridge))
	}

	paginatedResponse(c, "Bridges", size, page, resources, count, err)
}
```

**File:** core/web/presenters/bridges.go (L11-22)
```go
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

**File:** core/web/presenters/bridges.go (L29-41)
```go
// NewBridgeResource constructs a new BridgeResource
func NewBridgeResource(b bridges.BridgeType) *BridgeResource {
	return &BridgeResource{
		// Uses the name as the id...Should change this to the id
		JAID:                   NewJAID(b.Name.String()),
		Name:                   b.Name.String(),
		URL:                    b.URL.String(),
		Confirmations:          b.Confirmations,
		OutgoingToken:          b.OutgoingToken,
		MinimumContractPayment: b.MinimumContractPayment,
		UseConnectionManager:   b.UseConnectionManager,
		CreatedAt:              b.CreatedAt,
	}
```

**File:** core/bridges/bridge_type.go (L57-68)
```go
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

**File:** core/bridges/bridge_type.go (L70-101)
```go
// NewBridgeType returns a bridge type authentication (with plaintext
// password) and a bridge type (with hashed password, for persisting)
func NewBridgeType(btr *BridgeTypeRequest) (*BridgeTypeAuthentication,
	*BridgeType, error,
) {
	incomingToken := utils.NewSecret(24)
	outgoingToken := utils.NewSecret(24)
	salt := utils.NewSecret(24)

	hash, err := incomingTokenHash(incomingToken, salt)
	if err != nil {
		return nil, nil, err
	}

	return &BridgeTypeAuthentication{
		Name:                   btr.Name,
		URL:                    btr.URL,
		Confirmations:          btr.Confirmations,
		IncomingToken:          incomingToken,
		OutgoingToken:          outgoingToken,
		MinimumContractPayment: btr.MinimumContractPayment,
		UseConnectionManager:   btr.UseConnectionManager,
	}, &BridgeType{
		Name:                   btr.Name,
		URL:                    btr.URL,
		Confirmations:          btr.Confirmations,
		IncomingTokenHash:      hash,
		Salt:                   salt,
		OutgoingToken:          outgoingToken,
		MinimumContractPayment: btr.MinimumContractPayment,
		UseConnectionManager:   btr.UseConnectionManager,
	}, nil
```
