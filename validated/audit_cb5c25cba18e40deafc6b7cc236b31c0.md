### Title
Bridge Update silently nulls MinimumContractPayment via partial PATCH request - ([File: core/bridges/orm.go])

### Summary
`BridgeTypesController.Update` fetches the existing `BridgeType` by pointer but then calls `orm.UpdateBridgeType` which performs a full-column SQL `UPDATE` using the raw, unchecked fields from the attacker-supplied `BridgeTypeRequest` (`btr`), with no merge/fallback logic against the previously fetched value. Omitting `minimumContractPayment` (or sending `null`) in the PATCH body causes the field to unmarshal as `nil`, which is written directly into the `minimum_contract_payment` column, clearing the payment floor for all consumers of that shared bridge.

### Finding Description
The controller flow is: [1](#0-0) 

`bt` is fetched fresh from the DB at line 161, then `btr` is unmarshaled straight from the request body via `c.ShouldBindJSON(btr)` (line 171), validated only for negativity via `ValidateBridgeType` (which explicitly allows `nil`, `bt.MinimumContractPayment != nil && ...`) (`core/web/bridge_types_controller.go:48-51`), and then passed unmodified into `orm.UpdateBridgeType(ctx, &bt, btr)`.

`UpdateBridgeType` performs an unconditional column overwrite: [2](#0-1) 

There is no branch that preserves `bt.MinimumContractPayment` when `btr.MinimumContractPayment` is `nil`; the SQL statement always sets `minimum_contract_payment = $3` using `btr.MinimumContractPayment` directly. Since `MinimumContractPayment *assets.Link` in `BridgeTypeRequest` is a pointer field, JSON omission or explicit `null` both unmarshal to `nil`, and `nil` is written to the DB as SQL `NULL`, clearing whatever positive floor was previously set — regardless of what other jobs configured against that bridge assume.

The existing test `TestBridgeTypesController_Update_Success` only exercises a PATCH that omits `minimumContractPayment` while checking only the `url` field is updated — it does not assert that `minimumContractPayment` is preserved, so this regression is not caught: [3](#0-2) 

### Impact Explanation
A bridge-edit-role user editing a bridge they are authorized to modify (e.g., to update its `url`) can inadvertently or deliberately zero out the `MinimumContractPayment` floor for the *entire* shared `BridgeType`, since bridges are referenced by name across all jobs/specs that use them. This is a payment-floor bypass affecting all consumers of that bridge, not just the caller's own resources — matching an authorization/isolation exactness violation and a "unauthorized job run or fund movement" class impact (min-payment floor circumvention for TOB/duplicate job responses).

### Likelihood Explanation
Requires only bridge-edit role (a legitimate, lower-than-admin credential per the threat model) and a single PATCH request; no race condition or special timing needed. It is 100% reproducible: any PATCH to `/v2/bridge_types/:BridgeName` that omits `minimumContractPayment` will null it out.

### Recommendation
In `BridgeTypesController.Update` (or in `UpdateBridgeType`), merge the request onto the existing record instead of blind overwrite — e.g., if `btr.MinimumContractPayment == nil`, retain `bt.MinimumContractPayment` before calling the ORM, or change `UpdateBridgeType`'s SQL/binding to use `COALESCE`/conditional field selection so an absent field does not clear a previously-set value.

### Proof of Concept
Go handler-level integration test plan (extends `bridge_types_controller_test.go`):
1. Create a bridge via `app.BridgeORM().CreateBridgeType` with `MinimumContractPayment: assets.NewLinkFromJuels(100)`.
2. Send `PATCH /v2/bridge_types/:BridgeName` with body `{"name":"<name>","url":"http://yourbridge"}` (omitting `minimumContractPayment`).
3. Assert response is 200 OK.
4. Fetch the bridge again via `app.BridgeORM().FindBridge` and assert `MinimumContractPayment` still equals `assets.NewLinkFromJuels(100)` (currently it will be `nil`, proving the bug).
5. Repeat with explicit `"minimumContractPayment": null` in the body for the same result.

### Citations

**File:** core/web/bridge_types_controller.go (L160-182)
```go
	orm := btc.App.BridgeORM()
	bt, err := orm.FindBridge(ctx, taskType)
	if errors.Is(err, sql.ErrNoRows) {
		jsonAPIError(c, http.StatusNotFound, errors.New("bridge not found"))
		return
	}
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	if err := c.ShouldBindJSON(btr); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}
	if err := ValidateBridgeType(btr); err != nil {
		jsonAPIError(c, http.StatusBadRequest, err)
		return
	}
	if err := orm.UpdateBridgeType(ctx, &bt, btr); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
```

**File:** core/bridges/orm.go (L142-148)
```go
// UpdateBridgeType updates the bridge type.
func (o *orm) UpdateBridgeType(ctx context.Context, bt *BridgeType, btr *BridgeTypeRequest) error {
	stmt := "UPDATE bridge_types SET url = $1, confirmations = $2, minimum_contract_payment = $3, use_connection_manager = $4 WHERE name = $5 RETURNING *"
	err := o.ds.GetContext(ctx, bt, stmt, btr.URL, btr.Confirmations, btr.MinimumContractPayment, btr.UseConnectionManager, bt.Name)

	return err
}
```

**File:** core/web/bridge_types_controller_test.go (L245-269)
```go
func TestBridgeTypesController_Update_Success(t *testing.T) {
	t.Parallel()

	app := cltest.NewApplication(t)
	require.NoError(t, app.Start(t.Context()))
	client := app.NewHTTPClient(nil)

	bridgeName := testutils.RandomizeName("BRidgea")
	bt := &bridges.BridgeType{
		Name: bridges.MustParseBridgeName(bridgeName),
		URL:  cltest.WebURL(t, "http://mybridge"),
	}
	ctx := t.Context()
	require.NoError(t, app.BridgeORM().CreateBridgeType(ctx, bt))

	body := fmt.Sprintf(`{"name": "%s","url":"http://yourbridge"}`, bridgeName)
	ud := bytes.NewBufferString(body)
	resp, cleanup := client.Patch("/v2/bridge_types/"+bridgeName, ud)
	t.Cleanup(cleanup)
	cltest.AssertServerResponse(t, resp, http.StatusOK)

	ubt, err := app.BridgeORM().FindBridge(ctx, bt.Name)
	require.NoError(t, err)
	assert.Equal(t, cltest.WebURL(t, "http://yourbridge"), ubt.URL)
}
```
