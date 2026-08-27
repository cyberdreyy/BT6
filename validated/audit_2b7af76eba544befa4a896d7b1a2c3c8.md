Confirmed: `authv2.GET("/bridge_types/:BridgeName", bt.Show)` at [1](#0-0) has no `auth.RequiresEditRole`/`RequiresRunRole` wrapper, unlike Create/Update/Destroy which do require edit role. This means any authenticated user, including view-role, can call `Show`.

### Title
BridgeTypesController.Show leaks plaintext OutgoingToken to view-role users via GET /v2/bridge_types/:BridgeName - ([File: core/web/bridge_types_controller.go])

### Summary
The `Show` (and `Index`/`Update`) handlers of `BridgeTypesController` build the response via `presenters.NewBridgeResource`, which always copies the bridge's plaintext `OutgoingToken` into the JSON response with no `omitempty`/redaction, and this route is registered without any `RequiresEditRole`/`RequiresRunRole` guard, so a view-role authenticated user can retrieve the bridge's outgoing token secret.

### Finding Description
`BridgeTypesController.Show` looks up the bridge by name and calls `presenters.NewBridgeResource(bt)` unconditionally: [2](#0-1) 

`NewBridgeResource` copies `b.OutgoingToken` directly into the `BridgeResource.OutgoingToken` field, which is tagged `json:"outgoingToken"` (no `omitempty`, no redaction): [3](#0-2) 

The `bridges.BridgeType` struct stores `OutgoingToken` in plaintext (only the incoming-token is hashed with salt into `IncomingTokenHash`): [4](#0-3) 

Route registration in `v2Routes` shows `Show` is bound with no role wrapper, while `Create`, `Update`, and `Destroy` are wrapped in `auth.RequiresEditRole`: [5](#0-4) 

Any authenticated session/token user — including one with `UserRoleView` — passes the generic `auth.Authenticate` middleware for `authv2` and reaches `Show` without any further role check (`RequiresEditRole`/`RequiresRunRole` are only used to gate the mutating and index/other routes elsewhere), as confirmed by the role-check logic in `RequiresEditRole`/`RequiresRunRole`: [6](#0-5) 

The Index endpoint (`authv2.GET("/bridge_types", paginatedRequest(bt.Index))`) is similarly unguarded and also returns `OutgoingToken` for every bridge in the list.

### Impact Explanation
The `OutgoingToken` is the credential the Chainlink node itself sends outward to the external adapter/bridge for authenticating its own requests (analogous to a service secret); a view-only user obtaining it can impersonate the node when calling the external adapter, or use it to further probe/pivot on the bridge integration. This is a genuine secret-disclosure vulnerability reachable by a least-privileged authenticated role, matching a "Sensitive Data Disclosure to unauthorized user" bounty impact class — comparable in severity to leaking `UserResource.TokenKey`/`HashedPassword` if that presenter failed to redact them.

### Likelihood Explanation
Exploitation requires nothing beyond a valid view-role session or API token — the lowest privilege authenticated role in the system. Any GET request to `/v2/bridge_types/:BridgeName` (or `/v2/bridge_types` for the index) by such a user immediately returns the token in the JSON response body; the flaw is deterministic and fully repeatable.

### Recommendation
Add `auth.RequiresEditRole` (or at minimum `RequiresRunRole`) to the `Show` and `Index` bridge_types routes, mirroring `Create`/`Update`/`Destroy`; alternatively/also redact `OutgoingToken` in `presenters.NewBridgeResource` for view-role callers (e.g., return only a masked indicator unless the caller has edit/admin role), similar to how `UserResource` avoids returning `HashedPassword`/`TokenKey` and only exposes `HasActiveApiToken`.

### Proof of Concept
Go handler-level integration test (in `core/web/bridge_types_controller_test.go` style):
1. Create an admin-owned bridge via `app.BridgeORM().CreateBridgeType` with a known `OutgoingToken` value.
2. Create a session/API token for a user with `sessions.UserRoleView`.
3. Using that view-role client, issue `client.Get("/v2/bridge_types/<name>")`.
4. Assert HTTP 200 and parse response JSON; assert `data.attributes.outgoingToken` equals the plaintext secret — demonstrating disclosure — then assert this should instead be `401/403` or redacted per the fix.
5. Repeat for `GET /v2/bridge_types` (Index) confirming the same field leaks in the list response.

### Citations

**File:** core/web/router.go (L268-273)
```go
		bt := BridgeTypesController{app}
		authv2.GET("/bridge_types", paginatedRequest(bt.Index))
		authv2.POST("/bridge_types", auth.RequiresEditRole(bt.Create))
		authv2.GET("/bridge_types/:BridgeName", bt.Show)
		authv2.PATCH("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Update))
		authv2.DELETE("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Destroy))
```

**File:** core/web/bridge_types_controller.go (L125-146)
```go
func (btc *BridgeTypesController) Show(c *gin.Context) {
	ctx := c.Request.Context()
	name := c.Param("BridgeName")

	taskType, err := bridges.ParseBridgeName(name)
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	bt, err := btc.App.BridgeORM().FindBridge(ctx, taskType)
	if errors.Is(err, sql.ErrNoRows) {
		jsonAPIError(c, http.StatusNotFound, errors.New("bridge not found"))
		return
	}
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	jsonAPIResponse(c, presenters.NewBridgeResource(bt), "bridge")
}
```

**File:** core/web/presenters/bridges.go (L10-41)
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

// GetName implements the api2go EntityNamer interface
func (r BridgeResource) GetName() string {
	return "bridges"
}

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

**File:** core/web/auth/auth.go (L200-236)
```go
// RequiresRunRole extracts the user object from the context, and asserts the user's role is at least
// 'run'
func RequiresRunRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}

// RequiresEditRole extracts the user object from the context, and asserts the user's role is at least
// 'edit'
func RequiresEditRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView || user.Role == clsessions.UserRoleRun {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
```
