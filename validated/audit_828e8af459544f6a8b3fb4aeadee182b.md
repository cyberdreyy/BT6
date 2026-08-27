### Title
Missing role check on `GET /v2/bridge_types/:BridgeName` discloses bridge `outgoingToken` to view-role users - ([File: core/web/bridge_types_controller.go])

### Finding Description
`v2Routes` registers the bridge routes as:
`authv2.GET("/bridge_types/:BridgeName", bt.Show)` with no `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper, unlike the sibling `Create`, `Update`, and `Destroy` routes which are explicitly wrapped with `auth.RequiresEditRole` [1](#0-0) . The `authv2` group only requires that a request be authenticated via token or session (`auth.Authenticate(...auth.AuthenticateByToken, auth.AuthenticateBySession)`), with no minimum role enforced by default [2](#0-1) . `BridgeTypesController.Show` looks up the bridge by name and directly returns `presenters.NewBridgeResource(bt)` with no additional role check [3](#0-2) . `NewBridgeResource` unconditionally copies `OutgoingToken` from the `bridges.BridgeType` model into the JSON response with the tag `json:"outgoingToken"` (not `omitempty`, unlike `IncomingToken`) [4](#0-3) . `OutgoingToken` is a credential generated at bridge creation time (`utils.NewSecret(24)`) and stored in plaintext on the `BridgeType` model, used to authenticate outbound calls to the external adapter [5](#0-4) . Consequently, any authenticated session/token — including a view-only role — can call `GET /v2/bridge_types/:BridgeName` and receive this secret in the response body.

### Impact Explanation
This matches Chainlink's "secret/credential disclosure" impact class: a low-privilege (view-role) authenticated user can read the `outgoingToken` for any bridge, a secret that is otherwise supposed to be restricted since mutating operations on bridges require edit role. Possessing the `OutgoingToken` allows the attacker to impersonate the node when authenticating to the external adapter for that bridge (depending on how the adapter validates the token), which is a credential-exposure/impersonation risk beyond the attacker's intended read-only privileges.

### Likelihood Explanation
Low barrier: only requires a valid authenticated session or API token with the default/view role (no edit or admin role needed), and knowledge/guessing of a bridge name (bridge names can also be enumerated via `GET /v2/bridge_types` which is likewise unguarded by role in this router). The request is a single unauthenticated-role GET call, fully repeatable and deterministic.

### Recommendation
Wrap `bt.Show` (and likely `bt.Index`) with `auth.RequiresEditRole` (or at minimum ensure `OutgoingToken` is excluded from the presenter for non-privileged roles), mirroring the protection already applied to `Create`, `Update`, and `Destroy`:
```go
authv2.GET("/bridge_types/:BridgeName", auth.RequiresEditRole(bt.Show))
```
Alternatively/additionally, mark `OutgoingToken` as sensitive and omit it from `BridgeResource` for read/list endpoints, only returning it when the caller has edit/admin role, similar to how `IncomingToken` is only populated on create.

### Proof of Concept
Go handler-level integration test plan (using existing test harness patterns from `core/web/bridge_types_controller_test.go` and `core/web/auth/auth_test.go`):
1. Create an app/test client via `cltest.NewApplication` and `bridges.BridgeType` fixture (e.g., `cltest.NewBridgeType` or via `orm.CreateBridgeType`) with a known `OutgoingToken`.
2. Create a user with `sessions.UserRoleView` (or equivalent lowest role) and authenticate via `POST /sessions` to obtain a session cookie (pattern from `core/web/auth/auth_test.go`).
3. Issue `GET /v2/bridge_types/<bridgeName>` using that view-role session cookie.
4. Assert: HTTP status `200 OK` (not `403 Forbidden`), and that the JSON response body's `data.attributes.outgoingToken` field equals the bridge's actual `OutgoingToken` value.
5. As a control, repeat the same request against `PATCH /v2/bridge_types/:BridgeName` and confirm it correctly returns `403` for the view-role session (demonstrating the inconsistency and confirming `RequiresEditRole` works as expected elsewhere), highlighting `Show` as the outlier.

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

**File:** core/bridges/bridge_type.go (L57-102)
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
