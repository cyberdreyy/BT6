### Title
View-role session users can retrieve a bridge's `OutgoingToken` via `GET /v2/bridge_types/:BridgeName` - ([File: core/web/bridge_types_controller.go])

### Summary
The `Show` handler for `/v2/bridge_types/:BridgeName` is registered without any role-restriction middleware, unlike the `Create`/`Update`/`Destroy` handlers on the same resource. It returns a `presenters.BridgeResource` whose `OutgoingToken` field is always serialized (no `omitempty`, no redaction), so any authenticated session user, including one with only `clsessions.UserRoleView`, can read the bridge's outgoing credential.

### Finding Description
In `core/web/router.go`, the bridge type routes are:
```go
authv2.GET("/bridge_types", paginatedRequest(bt.Index))
authv2.POST("/bridge_types", auth.RequiresEditRole(bt.Create))
authv2.GET("/bridge_types/:BridgeName", bt.Show)
authv2.PATCH("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Update))
authv2.DELETE("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Destroy))
``` [1](#0-0) 

Only `Create`, `Update`, and `Destroy` are wrapped with `auth.RequiresEditRole`; `Index` and `Show` are reachable by any user who passes the outer `authv2` session/token authentication group (which requires only a valid session cookie or API token, not a specific role) at [2](#0-1) .

`BridgeTypesController.Show` looks up the bridge by name and serializes it directly with `presenters.NewBridgeResource(bt)`:
```go
bt, err := btc.App.BridgeORM().FindBridge(ctx, taskType)
...
jsonAPIResponse(c, presenters.NewBridgeResource(bt), "bridge")
``` [3](#0-2) 

`presenters.NewBridgeResource` copies `b.OutgoingToken` into the resource, and the JSON tag has no `omitempty` (unlike `IncomingToken`, which is annotated as "only provided when creating a Bridge" and is `omitempty`):
```go
IncomingToken          string       `json:"incomingToken,omitempty"`
OutgoingToken          string       `json:"outgoingToken"`
...
func NewBridgeResource(b bridges.BridgeType) *BridgeResource {
	return &BridgeResource{
		...
		OutgoingToken: b.OutgoingToken,
		...
``` [4](#0-3) 

`bridges.BridgeType.OutgoingToken` is the secret used by the node to authenticate outbound requests to the external adapter (it is the credential the EA is meant to trust as coming from this node). Because `Show` performs no role check and the presenter never redacts `OutgoingToken`, the field is returned verbatim to any session user regardless of role, including `UserRoleView`.

### Impact Explanation
This is a credential/secret disclosure vulnerability: a low-privilege ("view"-only) authenticated user can obtain the `OutgoingToken` for any bridge by name and use it to impersonate the node when calling the corresponding external adapter (or replay/forge responses expected by the node), violating the stated invariant that bridge/EI credentials must never leave the node in API responses. This aligns with the "sensitive data exposure / secret disclosure" bounty impact class rather than fund loss, since it does not by itself move funds, but it does grant unauthorized access to a credential meant to be edit/admin-scoped.

### Likelihood Explanation
Exploitation requires only a valid low-privilege session (view role) or valid API token for the node and knowledge/guessing of an existing bridge name (bridge names are also exposed via the unauthenticated-role-gated `GET /v2/bridge_types` list, itself reachable without `RequiresEditRole`). No special timing or race condition is needed; the request is a single `GET`. This is fully deterministic and repeatable.

### Recommendation
- Wrap `GET /v2/bridge_types/:BridgeName` (and `GET /v2/bridge_types`) with an appropriate role check consistent with the intended access model (e.g., `auth.RequiresEditRole` or a new `RequiresViewRole` that still redacts secrets), and/or
- Redact `OutgoingToken` in `presenters.NewBridgeResource` for read paths (`Index`/`Show`), only including it (if at all) for privileged roles, mirroring how `IncomingToken` is already treated as write-only/`omitempty` and only populated explicitly in `Create`.

### Proof of Concept
Go handler-level integration test plan:
1. Set up a test app/router via existing helpers (see `core/web/bridge_types_controller_test.go` for patterns) with a bridge created via `POST /v2/bridge_types` using an edit/admin session, capturing the bridge name.
2. Create a second session authenticated as a user with `clsessions.UserRoleView` (see `core/web/auth/auth_test.go` for session/role helpers).
3. Issue `GET /v2/bridge_types/<bridgeName>` using the view-role session cookie.
4. Assert HTTP 200 (no role check blocks the request) and that the JSON response body's `data.attributes.outgoingToken` is present and non-empty, matching the bridge's stored `OutgoingToken`.
5. Contrast with `PATCH`/`DELETE` on the same route using the same view-role session, expecting a 401/403 due to `auth.RequiresEditRole`, demonstrating the inconsistency that `Show` alone lacks role enforcement.

### Citations

**File:** core/web/router.go (L245-249)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	{
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

**File:** core/web/presenters/bridges.go (L16-41)
```go
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
