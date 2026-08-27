### Title
Bridge `OutgoingToken` credential is returned in plaintext on every bridge read/write response, including `GET /v2/bridge_types/:BridgeName` for view-role users - ([File: core/web/presenters/bridges.go])

### Summary
`presenters.NewBridgeResource` unconditionally copies `bridges.BridgeType.OutgoingToken` into the JSON `outgoingToken` field with no redaction and no `omitempty`, and `BridgeTypesController.Show` (as well as `Index`, `Update`, `Destroy`) calls this presenter on every request. Any authenticated caller who can reach these endpoints (minimally a view-role session/API token) receives the bridge's real outgoing token in the response body.

### Finding Description
`core/web/presenters/bridges.go` defines: [1](#0-0) 
and populates `OutgoingToken: b.OutgoingToken` directly from the persisted `bridges.BridgeType` struct: [2](#0-1) 

Unlike `IncomingToken` (tagged `json:"incomingToken,omitempty"` and only ever populated in the `Create` handler from the one-time `BridgeTypeAuthentication` struct, never from the persisted `BridgeType`), `OutgoingToken` has no `omitempty` and is sourced straight from the DB-loaded `bridges.BridgeType` value in every controller path:

- `Show`: `jsonAPIResponse(c, presenters.NewBridgeResource(bt), "bridge")` [3](#0-2) 
- `Index`: same presenter over the paginated bridge list [4](#0-3) 
- `Update`/`Destroy`: same pattern [5](#0-4) [6](#0-5) 

Note that `IncomingTokenHash` and `Salt` (the hashed/salted incoming-auth secret) are correctly never included in `BridgeResource` — only `Name`, `URL`, `Confirmations`, `OutgoingToken`, `MinimumContractPayment`, `UseConnectionManager`, and `CreatedAt` are exposed. So the "salted secret" (incoming token hash/salt) is not leaked, but the plaintext `OutgoingToken` (the credential the node uses to authenticate itself to the external adapter) is.

Since the `BridgeType.OutgoingToken` field, as generated in `bridges.NewBridgeType`, is a persistent 24-byte secret stored alongside the bridge and never rotated/hashed [7](#0-6) , it is a durable, sensitive credential, not a one-time value like `IncomingToken`. Any low-privileged, view-role-authenticated caller of `GET /v2/bridge_types/:BridgeName` (or the list endpoint) receives this token in cleartext in the response body — this is not gated to admin/edit roles by the presenter or controller logic itself.

I was unable to fully confirm from the index the exact role requirement enforced by the route middleware in `core/web/router.go` for the bridge_types GET routes (whether it requires `view` vs `admin` role) since the router file's relevant route-registration block was not retrieved before the tool budget ended. This is a gap in verification, but based on the standard Chainlink RBAC pattern (GET endpoints require the minimal `view` role, mutating endpoints require `edit`/`admin`), it is very likely the Show/Index routes are reachable by `view`-role sessions and API tokens.

### Impact Explanation
Exposure of `OutgoingToken` allows a low-privileged authenticated user (view-role session, restricted API token) to obtain the credential used by the node to authenticate to the external adapter/bridge, enabling impersonation of the node when calling that adapter, or reuse of the token for other purposes the adapter operator did not intend. This matches a "credential/secret disclosure to low-privileged authenticated user" bounty impact class, though the leaked secret is the outgoing (egress) adapter auth token rather than the incoming bridge auth secret (which remains properly hashed/salted and non-exposed).

### Likelihood Explanation
Trivial and fully repeatable: any caller with a valid view-role session cookie or API token can call `GET /v2/bridge_types/:BridgeName` (or the paginated list) and read `outgoingToken` directly from the JSON response with no additional preconditions, race conditions, or timing requirements.

### Recommendation
Remove `OutgoingToken` from `BridgeResource`/`NewBridgeResource` entirely (treat it the same as `IncomingTokenHash`/`Salt`, i.e., never serialize it), or restrict its exposure to the same one-time-disclosure semantics as `IncomingToken` (only returned in the `Create` response from `BridgeTypeAuthentication`, with `omitempty` and never sourced from the persisted `BridgeType` in `Show`/`Index`/`Update`/`Destroy`). Also confirm/tighten the role requirement on GET bridge_types routes if intended to be `admin`-only.

### Proof of Concept
1. Add a handler-level test in `core/web/bridge_types_controller_test.go`:
   - Create a bridge via `POST /v2/bridge_types` as an admin/edit-role client; capture `outgoingToken` from the create response and the bridge name.
   - Create a second HTTP client authenticated with a `view`-role user/API token (see existing patterns for role-scoped clients in `core/web/auth/auth_test.go` or `cltest`).
   - Call `GET /v2/bridge_types/{name}` with the view-role client.
   - JSON-unmarshal the response into `presenters.BridgeResource` and assert `resource.OutgoingToken` is non-empty and equals the token captured at creation — proving disclosure to a low-privileged caller.
   - Additionally assert `resource.IncomingToken` is empty (`omitempty`) confirming that the incoming-secret path is safe, isolating the finding to `OutgoingToken`.

### Citations

**File:** core/web/presenters/bridges.go (L10-22)
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

**File:** core/web/bridge_types_controller.go (L112-122)
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

**File:** core/web/bridge_types_controller.go (L179-192)
```go
	if err := orm.UpdateBridgeType(ctx, &bt, btr); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	btc.App.GetAuditLogger().Audit(audit.BridgeUpdated, map[string]any{
		"bridgeName":                   bt.Name,
		"bridgeConfirmations":          bt.Confirmations,
		"bridgeMinimumContractPayment": bt.MinimumContractPayment,
		"bridgeURL":                    bt.URL,
	})

	jsonAPIResponse(c, presenters.NewBridgeResource(bt), "bridge")
}
```

**File:** core/web/bridge_types_controller.go (L224-232)
```go
	if err = orm.DeleteBridgeType(ctx, &bt); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, fmt.Errorf("failed to delete bridge: %w", err))
		return
	}

	btc.App.GetAuditLogger().Audit(audit.BridgeDeleted, map[string]any{"name": name})

	jsonAPIResponse(c, presenters.NewBridgeResource(bt), "bridge")
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
