Confirmed. Route registration shows `GET /v2/bridge_types` (`Index`) and `GET /v2/bridge_types/:BridgeName` (`Show`) are only wrapped in the base `authv2` authentication middleware (session/token), with no `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper, unlike the sibling `POST`/`PATCH`/`DELETE` routes which do use `auth.RequiresEditRole`. [1](#0-0) 

### Title
View-role users can read bridge OutgoingToken via GET /v2/bridge_types (no role gate, no presenter redaction) - ([File: core/web/bridge_types_controller.go])

### Summary
The `Index` and `Show` handlers of `BridgeTypesController` are registered without any role-check middleware, and `presenters.NewBridgeResource` always copies `BridgeType.OutgoingToken` into the JSON response with no `omitempty` or role-based filtering. Any authenticated user, including view/run-only roles, can retrieve every bridge's `outgoingToken`.

### Finding Description
`v2Routes` registers `GET /v2/bridge_types` and `GET /v2/bridge_types/:BridgeName` inside the `authv2` group, which only requires session/token authentication (`auth.Authenticate(... AuthenticateByToken, AuthenticateBySession)`), with no `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper as used on the mutating routes (`POST`, `PATCH`, `DELETE`) for the same resource. [2](#0-1) 

`BridgeTypesController.Index` and `.Show` fetch the full `bridges.BridgeType` from the ORM and pass it straight into `presenters.NewBridgeResource`. [3](#0-2) 

`NewBridgeResource` unconditionally sets `OutgoingToken: b.OutgoingToken`, and the `BridgeResource.OutgoingToken` field is tagged `json:"outgoingToken"` with no `omitempty` — unlike `IncomingToken`, which is tagged `omitempty` and only populated in the `Create` handler. [4](#0-3) 

`BridgeType.OutgoingToken` is the plaintext secret token used by the node to authenticate the bridge's response back to the node (i.e., presented by the external adapter to prove it's talking to the legitimate caller / used for outbound auth), distinct from `IncomingTokenHash`+`Salt` which protect the inbound token. [5](#0-4) 

Since there is no role check on these two GET routes, any user authenticated with only the view/run role (the minimum authenticated role in the system, as evidenced by `auth.RequiresRunRole`/`auth.RequiresEditRole`/`auth.RequiresAdminRole` being used elsewhere for stricter routes) can call `GET /v2/bridge_types/:BridgeName` or `GET /v2/bridge_types` and receive the `outgoingToken` field in the response for every bridge in the node.

### Impact Explanation
This discloses the bridge `OutgoingToken` secret to any authenticated low-privilege user, allowing that user to impersonate the node's outbound identity toward the external adapter/bridge, i.e., forge/validate responses as if they came from the legitimate bridge caller. This matches the "Secrets never leave (redaction)" / credential-disclosure impact class — a low-privileged reader obtains a secret token intended only for edit/admin-level operators, enabling response forgery from that identity.

### Likelihood Explanation
Trivial and fully repeatable: any valid session cookie or API token for a user provisioned with only view or run role is sufficient. No additional exploitation steps, no timing or race conditions — a single `GET` request to a documented, always-available endpoint returns the secret in the JSON body.

### Recommendation
Add `auth.RequiresEditRole` (or `RequiresAdminRole`) to the `GET /v2/bridge_types` and `GET /v2/bridge_types/:BridgeName` routes in `core/web/router.go`, and/or strip `OutgoingToken` from `BridgeResource` for non-privileged callers (add `omitempty` and only populate it for edit/admin roles, mirroring the existing `IncomingToken` handling pattern in `Create`).

### Proof of Concept
Go handler-level integration test plan (using existing test scaffolding in `core/web/bridge_types_controller_test.go` and `core/internal/cltest`):
1. Create a node app and a bridge via admin/edit-role client (`POST /v2/bridge_types`), capturing the returned `outgoingToken`.
2. Create a second client authenticated with a session/token provisioned with only `view` (or `run`) role.
3. As the view-role client, call `GET /v2/bridge_types/:BridgeName` for the created bridge.
4. Assert HTTP 200 and that the response body's `outgoingToken` field is non-empty and equals the value set at creation — demonstrating disclosure.
5. Repeat for `GET /v2/bridge_types` (Index/paginated list) confirming `outgoingToken` appears for every bridge returned.
6. Expected post-fix behavior: request either returns 401/403 for the view-role client, or the `outgoingToken` field is omitted/empty in the JSON response.

### Citations

**File:** core/web/router.go (L245-273)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	{
		uc := UserController{app}
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
		authv2.PATCH("/user/password", uc.UpdatePassword)
		authv2.POST("/user/token", uc.NewAPIToken)
		authv2.POST("/user/token/delete", uc.DeleteAPIToken)

		wa := NewWebAuthnController(app)
		authv2.GET("/enroll_webauthn", wa.BeginRegistration)
		authv2.POST("/enroll_webauthn", wa.FinishRegistration)

		eia := ExternalInitiatorsController{app}
		authv2.GET("/external_initiators", paginatedRequest(eia.Index))
		authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
		authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))

		bt := BridgeTypesController{app}
		authv2.GET("/bridge_types", paginatedRequest(bt.Index))
		authv2.POST("/bridge_types", auth.RequiresEditRole(bt.Create))
		authv2.GET("/bridge_types/:BridgeName", bt.Show)
		authv2.PATCH("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Update))
		authv2.DELETE("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Destroy))
```

**File:** core/web/bridge_types_controller.go (L112-146)
```go
func (btc *BridgeTypesController) Index(c *gin.Context, size, page, offset int) {
	ctx := c.Request.Context()
	bridges, count, err := btc.App.BridgeORM().BridgeTypes(ctx, offset, size)

	resources := make([]presenters.BridgeResource, 0, len(bridges))
	for _, bridge := range bridges {
		resources = append(resources, *presenters.NewBridgeResource(bridge))
	}

	paginatedResponse(c, "Bridges", size, page, resources, count, err)
}

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
