### Title
BridgeTypesController.Show discloses bridge OutgoingToken/URL to any authenticated low-privilege (view-role) session - ([File: core/web/bridge_types_controller.go])

### Summary
The `GET /v2/bridge_types/:BridgeName` route is registered without any role wrapper (`auth.RequiresEditRole` etc.), only requiring the generic `auth.Authenticate` middleware. `BridgeTypesController.Show` serializes the bridge via `presenters.NewBridgeResource`, which always includes the plaintext `OutgoingToken` field and the bridge `URL`, with no redaction based on caller role.

### Finding Description
The route table shows: [1](#0-0) 
`bt.Show` is exposed at `authv2.GET("/bridge_types/:BridgeName", bt.Show)` with no `auth.RequiresEditRole`/`RequiresAdminRole` wrapper, unlike `Create`, `Update`, and `Destroy` on the same controller which are wrapped in `auth.RequiresEditRole`.

`Show` fetches the bridge and returns it unfiltered: [2](#0-1) 

`presenters.NewBridgeResource` copies `OutgoingToken` and `URL` directly from the `bridges.BridgeType` model into the JSON response with no role-based conditional/redaction logic: [3](#0-2) 

The `BridgeType` model stores the outgoing token in plaintext (`OutgoingToken string`), only the *incoming* token is stored hashed (`IncomingTokenHash`): [4](#0-3) 

Since the route requires only `auth.Authenticate` (any valid session or API token, regardless of role — view, run, edit, admin), any authenticated low-privilege user can call `GET /v2/bridge_types/mybridge` and receive the bridge's `outgoingToken` and full `URL` (which may embed credentials, e.g. `https://user:pass@host/path`) in the response body.

### Impact Explanation
This is a confidentiality/secret-disclosure issue: a low-privileged authenticated user (view-role) can read another bridge's `OutgoingToken`, which the node uses to authenticate itself to the external adapter, and the bridge's raw `URL`, which can contain embedded credentials or internal-network endpoint details. This maps to Chainlink's "sensitive data exposure / credential disclosure" bounty class, though the operational impact is limited by the fact this token is used by the node to authenticate outbound calls to the adapter (not to authenticate calls into the node), so its disclosure alone does not directly grant control over node funds or job execution — the primary loss is confidentiality of bridge endpoint credentials/URL.

### Likelihood Explanation
Trivial to exploit: only requires possession of any valid authenticated session/API token (the lowest-privilege "view" role suffices, since the route has no elevated-role wrapper) and knowledge of a valid bridge name (also disclosable via unwrapped `Index`, which uses the same unredacted presenter). No additional preconditions beyond baseline API access.

### Recommendation
Add role gating consistent with the other mutating bridge routes, or redact secret fields in `presenters.NewBridgeResource` based on caller role: only include `OutgoingToken` (and mask/omit the URL's userinfo component) for callers with edit/admin role; for view-role callers, return the resource with `OutgoingToken` and any URL credentials redacted, similar to how `IncomingToken` is already only surfaced on `Create`.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/bridge_types_controller_test.go`):
1. Start test app (`cltest.NewApplication`), create a bridge with `URL` containing embedded userinfo (e.g., `https://user:secret@adapter.example.com`) via `app.BridgeORM().CreateBridgeType`.
2. Create an HTTP client authenticated with a *view-role* session/API token (not edit/admin) — reuse `cltest` helpers for constructing a view-only session.
3. Issue `client.Get("/v2/bridge_types/<name>")`.
4. Assert `http.StatusOK` (proving no role check blocks the request).
5. Parse the JSON:API response into `presenters.BridgeResource` and assert that `resource.OutgoingToken != ""` and `resource.URL` still contains the embedded credential — demonstrating the secret is returned to a view-role caller.
6. Expected fix behavior: after remediation, the same request from a view-role token should either be rejected (403) or return `resource.OutgoingToken == ""`/redacted URL, while an edit/admin-role caller still receives the full data.

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

**File:** core/web/presenters/bridges.go (L10-42)
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
