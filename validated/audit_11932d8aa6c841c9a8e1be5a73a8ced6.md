### Title
Unrestricted-role disclosure of bridge `OutgoingToken` secret via `GET /v2/bridge_types/:BridgeName` (and `/v2/bridge_types`) - ([File: core/web/router.go])

### Summary
`authv2.GET("/bridge_types/:BridgeName", bt.Show)` (and the sibling `authv2.GET("/bridge_types", paginatedRequest(bt.Index))`) are registered without `auth.RequiresEditRole`/`RequiresAdminRole`/`RequiresRunRole`, so any authenticated user — including the lowest `view` role — can call them. Both handlers return a `presenters.BridgeResource` that includes the bridge's `OutgoingToken`, a secret credential used to authenticate outbound requests to the external adapter.

### Finding Description
`v2Routes` in `core/web/router.go` wraps most mutating bridge routes with role checks (`auth.RequiresEditRole(bt.Create)`, `auth.RequiresEditRole(bt.Update)`, `auth.RequiresEditRole(bt.Destroy)`), but leaves `bt.Show` and `bt.Index` unwrapped: [1](#0-0) 

Because the `authv2` group itself only requires successful authentication (session or API token) via `auth.Authenticate`, with no baseline role gate, any authenticated principal of any role reaches `BridgeTypesController.Show`/`Index` directly: [2](#0-1) 

`Show` looks up the bridge and serializes it with `presenters.NewBridgeResource`, with no role-based field redaction: [3](#0-2) 

`presenters.NewBridgeResource` copies `OutgoingToken` from the stored `bridges.BridgeType` directly into the JSON response: [4](#0-3) 

The `OutgoingToken` is a generated secret (`utils.NewSecret(24)`) stored in plaintext on the `BridgeType` record and used by the node to authenticate itself to the external adapter when sending outbound results: [5](#0-4) 

The role check helpers (`RequiresRunRole`, `RequiresEditRole`, `RequiresAdminRole`) exist precisely to gate handlers above the `view` role, and are consistently used for bridge mutation endpoints but omitted for the read endpoints: [6](#0-5) 

The `IncomingToken` (used by external callers to authenticate *to* the node) is deliberately only returned on `Create`, per the code comment "The IncomingToken is only provided when creating a Bridge" — showing the project's own intent that at least one bridge secret should not be readable after creation. However `OutgoingToken` has no such comment/guard and is returned unconditionally by `Show`/`Index`, which is not documented anywhere as an intentional low-privilege-viewable secret.

### Impact Explanation
A `view`-role user (or any user with an API token, regardless of assigned role) can retrieve the `OutgoingToken` secret for any bridge on the node via `GET /v2/bridge_types/:BridgeName` or by enumerating `GET /v2/bridge_types`. This maps to Chainlink's "secret/credential disclosure" bounty impact class: exposure of a token that is meant to authenticate the node to the external adapter, obtainable by a user who should not have edit/admin-level visibility into bridge configuration. This does not directly move funds but grants a lower-privileged, authenticated attacker access to a bridge credential outside their intended role scope.

### Likelihood Explanation
Preconditions: attacker only needs any valid authenticated session or API token on the node (lowest `view` role is sufficient, or a run/edit-role token used for other purposes). No admin/host access is required, and the request is a single unauthenticated-role-check-bypassing GET — highly feasible and trivially repeatable, since it requires no special sequencing beyond one HTTP GET.

### Recommendation
Wrap `bt.Show` (and consider `bt.Index`) with `auth.RequiresEditRole` (or a new `RequiresViewerPlusRole` that at minimum excludes bare view-only sessions if view-role access to bridge metadata sans secrets is desired), and/or redact `OutgoingToken` from `BridgeResource` for non-edit/admin roles, mirroring the treatment already given to `IncomingToken`.

### Proof of Concept
Table-driven handler test in `core/web/bridge_types_controller_test.go`:
1. Seed a bridge with a known `OutgoingToken`.
2. For each role in `{admin, edit, run, view}`, authenticate a session/API token client with that role.
3. Call `GET /v2/bridge_types/:BridgeName`.
4. Assert: for `admin`/`edit` the response returns 200 with `outgoingToken` populated (current/expected behavior); for `run`/`view`, assert the request is rejected with 401/403 (per the fix) — currently all four roles succeed and return the secret, demonstrating the gap.

### Citations

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L269-273)
```go
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

**File:** core/web/presenters/bridges.go (L30-41)
```go
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

**File:** core/bridges/bridge_type.go (L63-101)
```go
	OutgoingToken          string
	MinimumContractPayment *assets.Link
	CreatedAt              time.Time
	UpdatedAt              time.Time
	UseConnectionManager   bool `json:"useConnectionManager"`
}

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

**File:** core/web/auth/auth.go (L219-236)
```go
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
