Based on the code, GET `/v2/bridge_types/:BridgeName` is registered without any role wrapper [1](#0-0) , unlike `Create`, `Update`, and `Destroy`, which are wrapped with `auth.RequiresEditRole`. The `Show` handler loads the bridge from the ORM and serializes it directly with `presenters.NewBridgeResource` [2](#0-1) .

`presenters.NewBridgeResource` copies `b.OutgoingToken` into the `OutgoingToken` JSON field **without `omitempty`**, meaning it is always present in the serialized response for `Index`, `Show`, `Update`, and `Destroy` [3](#0-2) . In contrast, `IncomingToken` has `omitempty` and is populated only in `Create`, where it's explicitly assigned from `bta.IncomingToken` after bridge creation [4](#0-3) ; it's not set from the ORM's `bridges.BridgeType` model on any read path, so `Show`/`Index`/`Update`/`Destroy` never leak `IncomingToken`.

However, `OutgoingToken` is not similarly protected. It is a credential the node uses to authenticate to the external bridge/adapter, and it is returned to any authenticated caller — not just edit+ role — through `GET /v2/bridge_types` (Index, also unwrapped) and `GET /v2/bridge_types/:BridgeName` (Show, unwrapped) [5](#0-4) . This is a genuine inconsistency: mutating actions on bridges require edit role, but reading the bridge's outgoing credential requires only session/token authentication (any role, including view-only).

### Title
View-role users can read bridge OutgoingToken credential via unauthorized GET endpoints - ([File: core/web/bridge_types_controller.go])

### Summary
`GET /v2/bridge_types` and `GET /v2/bridge_types/:BridgeName` are registered without `auth.RequiresEditRole`, unlike `Create`/`Update`/`Destroy`. `presenters.BridgeResource` always serializes `OutgoingToken` (no `omitempty`), so any authenticated view-role user can retrieve the bridge's outgoing authentication credential.

### Finding Description
`v2Routes` in `core/web/router.go` wraps `POST /bridge_types`, `PATCH /bridge_types/:BridgeName`, and `DELETE /bridge_types/:BridgeName` with `auth.RequiresEditRole`, but leaves `GET /bridge_types` and `GET /bridge_types/:BridgeName` open to any authenticated caller (session or API token) regardless of role. `BridgeTypesController.Show` fetches the bridge via `btc.App.BridgeORM().FindBridge` and returns it through `presenters.NewBridgeResource(bt)`, which copies `b.OutgoingToken` into a JSON field lacking `omitempty`. As a result, a view-role-authenticated user issuing `GET /v2/bridge_types/<name>` receives the bridge's `OutgoingToken` in the response, even though edit-role is required to create/modify/delete the bridge. `IncomingToken` is not leaked on this path since it's only populated in `Create` and marked `omitempty` elsewhere.

### Impact Explanation
`OutgoingToken` is the credential the node presents to the external bridge adapter for outbound requests. Its disclosure to a view-only-role user is a bridge credential exposure that breaks the intended separation between read-only ("view") and mutating ("edit"/"admin") roles for bridge configuration — matching a secret/credential disclosure via authorization-role bypass impact class.

### Likelihood Explanation
Trivial and fully repeatable: any user with a valid view-role session or API token can call `GET /v2/bridge_types/:BridgeName` (or `/v2/bridge_types` for all bridges) with no special conditions. No timing, race, or additional exploitation is required.

### Recommendation
Wrap `GET /v2/bridge_types` and `GET /v2/bridge_types/:BridgeName` with `auth.RequiresEditRole` (or a lower "view-restricted" gate) if `OutgoingToken` must remain in the response, or omit/redact `OutgoingToken` from `presenters.BridgeResource` for read endpoints regardless of caller role, mirroring the `IncomingToken` `omitempty`/create-only exposure pattern.

### Proof of Concept
1. Unit test in `core/web/presenters/bridges_test.go`: construct a `bridges.BridgeType` with a non-empty `OutgoingToken`, call `NewBridgeResource`, marshal to JSON, and assert `outgoingToken` is absent/empty regardless of any caller-role context (currently it will always be present, proving the presenter doesn't redact by role).
2. Handler-level integration test in `core/web/bridge_types_controller_test.go`: create a bridge with edit/admin role (capturing its `OutgoingToken`), then authenticate as a view-role user/API token and call `GET /v2/bridge_types/:BridgeName`; assert the response body contains the same `outgoingToken` value, confirming disclosure to an under-privileged role.

### Citations

**File:** core/web/router.go (L269-273)
```go
		authv2.GET("/bridge_types", paginatedRequest(bt.Index))
		authv2.POST("/bridge_types", auth.RequiresEditRole(bt.Create))
		authv2.GET("/bridge_types/:BridgeName", bt.Show)
		authv2.PATCH("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Update))
		authv2.DELETE("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Destroy))
```

**File:** core/web/bridge_types_controller.go (L98-99)
```go
	resource := presenters.NewBridgeResource(*bt)
	resource.IncomingToken = bta.IncomingToken
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
