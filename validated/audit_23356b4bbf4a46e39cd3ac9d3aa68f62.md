### Title
View-role authenticated user can read bridge OutgoingToken/URL credentials via GET /v2/bridge_types/:BridgeName - ([File: core/web/bridge_types_controller.go])

### Summary
The `Show` route for bridge types is registered without any role gate (`authv2.GET("/bridge_types/:BridgeName", bt.Show)`), unlike `Create`/`Update`/`Destroy` which are wrapped in `auth.RequiresEditRole`. The presenter `BridgeResource` used by `Show` serializes `OutgoingToken` and the raw `URL` (which may embed basic-auth credentials) into the JSON response, exposing external-adapter secrets to any authenticated caller regardless of role.

### Finding Description
`core/web/router.go` registers bridge routes as: [1](#0-0) 
Only `Create`, `Update`, and `Destroy` are wrapped with `auth.RequiresEditRole`; `Show` (and `Index`) require only `authv2` authentication (session or API token), with no role check.

`BridgeTypesController.Show` fetches the bridge and returns it via `presenters.NewBridgeResource(bt)`: [2](#0-1) 

`BridgeResource` includes `OutgoingToken` (not `omitempty`, not redacted) and `URL` as plain strings: [3](#0-2) 

`BridgeType.OutgoingToken` is the actual secret token the node sends to the external adapter (set via `Authorization` header or similar when calling out), generated in `NewBridgeType`: [4](#0-3) 

Only `IncomingTokenHash` (the hash used to authenticate *inbound* calls from the adapter) is hashed/salted; `OutgoingToken` is stored and returned in plaintext. Because `Show` has no role gate, any authenticated user — including a viewer-role or restricted-role session/API-token holder — can call `GET /v2/bridge_types/:BridgeName` and receive the full `OutgoingToken` plus the `URL`, which may embed inline basic-auth credentials (`https://user:pass@host/...`), in the JSON response.

### Impact Explanation
This matches the "sensitive secret/credential disclosure" bounty class: an attacker with only a low-privilege (view) node account or restricted API token can extract the outgoing bridge token and any embedded URL credentials for every configured bridge. With the `OutgoingToken`, the attacker can impersonate the Chainlink node to the external adapter (send authenticated requests as the node), potentially manipulating data returned to job pipelines or exfiltrating further secrets from the adapter side.

### Likelihood Explanation
Minimal precondition: any valid session cookie or API token for a low-privilege ("view") user is sufficient — no edit/admin role, no additional exploitation steps. The request is a single unauthenticated-role-gated `GET` to a well-known, documented endpoint (`/v2/bridge_types/:BridgeName`), fully repeatable and requires no timing or race conditions.

### Recommendation
- Add `auth.RequiresEditRole` (or at minimum a role check excluding view-only) to the `Show` (and `Index`) bridge routes in `core/web/router.go`, consistent with `Create`/`Update`/`Destroy`.
- Redact `OutgoingToken` from `BridgeResource` for read (`Show`/`Index`) responses (e.g., mark `omitempty` and never populate it outside of `Create`, similar to how `IncomingToken` is only set in `Create`), or strip credentials embedded in `URL` before serialization.

### Proof of Concept
Go handler-level integration test plan (in `core/web/bridge_types_controller_test.go`):
1. Create an application with a bridge whose `URL` contains embedded basic-auth credentials (e.g. `http://user:secret@example.com/adapter`) and let `NewBridgeType` generate an `OutgoingToken`.
2. Create a session/API token for a user with the lowest role (e.g., `sessions.UserRoleView`).
3. Issue `GET /v2/bridge_types/{bridgeName}` authenticated as the view-role user.
4. Assert response status is `200 OK` (not `403 Forbidden`), demonstrating the missing role gate.
5. Parse the JSON body and assert that `data.attributes.outgoingToken` equals the real `OutgoingToken` value and `data.attributes.url` contains the embedded credentials — proving secret disclosure to a non-privileged caller.
6. As a regression check for the fix, re-run the same test expecting `403 Forbidden` (or `outgoingToken`/URL credentials redacted) once `RequiresEditRole`/presenter redaction is applied.

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

**File:** core/bridges/bridge_type.go (L55-101)
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
