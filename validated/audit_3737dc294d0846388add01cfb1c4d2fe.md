### Title
Bridge outgoing auth token disclosed to any authenticated user regardless of role via GET /v2/bridge_types/:BridgeName - ([File: core/web/router.go])

### Summary
The route `GET /v2/bridge_types/:BridgeName` is registered in `v2Routes` with only the base `authv2` authentication middleware and no `auth.RequiresEditRole`/`auth.RequiresRunRole` wrapper, unlike sibling routes `POST/PATCH/DELETE /v2/bridge_types`. `BridgeTypesController.Show` returns `presenters.NewBridgeResource(bt)`, whose `OutgoingToken` field is serialized unconditionally (no `omitempty`, no role-based redaction), exposing the bridge's outgoing authentication secret to any authenticated session/API token regardless of role.

### Finding Description
In `core/web/router.go`, the bridge_types routes are: [1](#0-0) 
`bt.Create`, `bt.Update`, and `bt.Destroy` are wrapped with `auth.RequiresEditRole`, but `bt.Show` (and `bt.Index`) is not wrapped with any role check — only membership in the `authv2` group, which merely requires successful authentication via token or session, with no minimum role enforced.

`BridgeTypesController.Show` fetches the bridge and directly serializes it: [2](#0-1) 

The presenter that builds the response includes `OutgoingToken` as a plain, non-omitempty JSON field, populated directly from the stored `BridgeType.OutgoingToken` cleartext value: [3](#0-2) 

`BridgeType.OutgoingToken` is generated as a plaintext secret at bridge creation time (unlike `IncomingToken`, which is hashed/salted before storage and never persisted in cleartext): [4](#0-3) 

This `OutgoingToken` is the credential the Chainlink node attaches when calling out to the external bridge adapter HTTP endpoint (used to authenticate the node to the adapter). Because `Show` is reachable by any authenticated principal — including a view-role user or a restricted API token — and the presenter never redacts `OutgoingToken` for non-edit roles, any authenticated low-privilege user can retrieve this outbound credential for any bridge by name.

### Impact Explanation
Exposure of `OutgoingToken` lets a low-privilege authenticated user (view-role) impersonate the node when calling the external bridge adapter (since that token is what the adapter uses to authenticate inbound calls from the node), or otherwise misuse/replay the node's integration credential outside the node's control. This matches the "sensitive credential/secret disclosure" bounty impact class rather than a full authentication bypass, since the attacker still needs some valid low-privilege authenticated session/token to reach the endpoint — but no edit/admin privilege is required, only credentials for the endpoint's minimal authentication tier.

### Likelihood Explanation
Preconditions are minimal: any valid authenticated session or API token, even one provisioned with the lowest ("view") role, or a run-role token, satisfies `authv2`'s authentication requirement since no role check is applied to this route. The bridge name is guessable/enumerable via `GET /v2/bridge_types` (also role-unrestricted) or via job spec definitions referencing bridge names. The exploit is a single unauthenticated-in-privilege-terms GET request, fully repeatable and requires no special conditions.

### Recommendation
Wrap `authv2.GET("/bridge_types/:BridgeName", bt.Show)` (and arguably `bt.Index`) with `auth.RequiresEditRole` (or a dedicated role check), consistent with `Create`/`Update`/`Destroy`. Alternatively/additionally, redact `OutgoingToken` from `BridgeResource` for non-edit-role callers (e.g., add `omitempty` and only populate it when the caller's role is edit/admin), similar to how `IncomingToken` is only ever populated transiently at creation time.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/bridge_types_controller_test.go`):
1. Create an application and a bridge type via `app.BridgeORM().CreateBridgeType` with a known `OutgoingToken`.
2. Create an HTTP client authenticated as a view-role user (`app.NewHTTPClient(&cltest.User{Role: sessions.UserRoleView})` or equivalent view-role API token, mirroring patterns in `core/web/auth/auth_test.go`).
3. Send `client.Get("/v2/bridge_types/<bridgeName>")`.
4. Assert `http.StatusOK` (confirming no role check blocks the request).
5. Parse JSON API response into `presenters.BridgeResource` and assert `resource.OutgoingToken` equals the known secret, demonstrating disclosure — expected (post-fix) result: request should be rejected with `403 Forbidden` for view-role, or `OutgoingToken` field should be empty/redacted in the response.

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

**File:** core/web/bridge_types_controller.go (L124-146)
```go
// Show returns the details of a specific Bridge.
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

**File:** core/bridges/bridge_type.go (L70-102)
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
}
```
