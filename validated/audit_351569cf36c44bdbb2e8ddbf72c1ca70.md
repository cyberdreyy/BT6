### Title
BridgeTypesController.Show leaks the plaintext bridge OutgoingToken to any view-role authenticated user - ([File: core/web/bridge_types_controller.go])

### Summary
`BridgeTypesController.Show` (mapped to `GET /v2/bridge_types/:BridgeName`) builds its response via `presenters.NewBridgeResource(bt)`, which always copies `BridgeType.OutgoingToken` into a non-`omitempty` JSON field `outgoingToken`. Since this route only requires the view role (per the router's role mapping referenced in the question), any authenticated view-role user can retrieve the plaintext outgoing credential for any configured bridge.

### Finding Description
`Show` fetches the bridge with `btc.App.BridgeORM().FindBridge(ctx, taskType)` and responds with `jsonAPIResponse(c, presenters.NewBridgeResource(bt), "bridge")` [1](#0-0) . `NewBridgeResource` unconditionally copies `b.OutgoingToken` into the resource: [2](#0-1) . The `BridgeResource.OutgoingToken` field has JSON tag `outgoingToken` with no `omitempty`, unlike `IncomingToken` which is tagged `omitempty` and is only ever populated at creation time: [3](#0-2) .

The underlying `BridgeType` struct stores `OutgoingToken` in plaintext (unlike `IncomingTokenHash`, which is salted/hashed): [4](#0-3) . `OutgoingToken` is a real secret — it is the credential the Chainlink node uses to authenticate itself when calling out to the external adapter/bridge, generated via `utils.NewSecret(24)` at bridge creation: [5](#0-4) .

Because `Create`, `Update`, `Destroy`, `Index`, and `Show` all funnel through `NewBridgeResource`, every one of these endpoints returns the plaintext `OutgoingToken` in the response body. `IncomingToken` (the token external callers use to authenticate *into* the node) is not leaked by `Show`, since `BridgeType` (as loaded from the ORM) has no `IncomingToken` field at all, only `IncomingTokenHash` — that field remains empty and is omitted due to `omitempty`. So the confidentiality violation is specific to `OutgoingToken`, not `IncomingToken`.

### Impact Explanation
This is a credential/secret disclosure to an authenticated-but-unprivileged (view-role) caller: any operator granted only read access to the Chainlink node's admin API can retrieve the plaintext outgoing bridge token for any bridge, and use it to impersonate the node when calling the associated external adapter/bridge endpoint, or reuse the credential outside the node's context. This matches the bounty class of secret/credential disclosure via authorization/role bypass.

### Likelihood Explanation
Requires only a valid view-role session/API token — no privileged, admin, or edit-role access needed. A single `GET /v2/bridge_types/<name>` request is sufficient and fully repeatable for every bridge configured on the node. No preconditions beyond normal, low-privilege authentication.

### Recommendation
Redact `OutgoingToken` from `BridgeResource` for read paths (`Show`, `Index`, `Update`, `Destroy`), or make the JSON field `omitempty`/masked, only exposing it (if ever) to edit/admin roles, and never on `GET`/list endpoints. Ideally, treat `OutgoingToken` the same way as `IncomingToken` — expose it only once, at creation, and store/represent it as a hash or reference thereafter.

### Proof of Concept
Go handler-level integration test plan:
1. Create a bridge type via `POST /v2/bridge_types` as an admin/edit user, capturing the persisted `OutgoingToken` (or seed it directly through `bridges.ORM.CreateBridgeType`).
2. Authenticate as a view-role-only session/API token (using the existing role-based test client helpers in `core/web/bridge_types_controller_test.go` / `core/internal/cltest`).
3. Issue `GET /v2/bridge_types/<name>` with the view-role client.
4. Assert HTTP 200 and decode the JSON API response into `presenters.BridgeResource`.
5. Assert that `resource.OutgoingToken` is non-empty and equals the known secret — demonstrating disclosure — then assert the fix makes this field absent/empty for view-role callers while still working for edit/admin roles when appropriate.

### Citations

**File:** core/web/bridge_types_controller.go (L135-146)
```go
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

**File:** core/web/presenters/bridges.go (L16-18)
```go
	// The IncomingToken is only provided when creating a Bridge
	IncomingToken          string       `json:"incomingToken,omitempty"`
	OutgoingToken          string       `json:"outgoingToken"`
```

**File:** core/web/presenters/bridges.go (L30-42)
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

**File:** core/bridges/bridge_type.go (L72-102)
```go
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
