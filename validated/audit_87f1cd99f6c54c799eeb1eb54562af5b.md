### Title
`BridgeTypesController.Create` leaks raw Postgres error details on unique-constraint conflict because the pgconn.PgError check runs on the wrong (already-nil) `err` variable - ([File: core/web/bridge_types_controller.go])

### Finding Description
In `BridgeTypesController.Create` [1](#0-0) , the flow is:

1. `bta, bt, err := bridges.NewBridgeType(btr)` at line 69 assigns `err`. This must be `nil` to proceed past its error check.
2. `orm.CreateBridgeType(ctx, bt)` is called and its result is captured in a **new local variable `e`**, not `err`:
```
if e := orm.CreateBridgeType(ctx, bt); e != nil {
    jsonAPIError(c, http.StatusInternalServerError, e)
    return
}
``` [2](#0-1) 
3. Immediately after, the code attempts to special-case Postgres unique-constraint conflicts:
```
var pgErr *pgconn.PgError
if errors.As(err, &pgErr) {
    ...
}
``` [3](#0-2) 

Because `err` here is the stale outer variable from the `NewBridgeType` call (which is guaranteed `nil` at this point, since the function already returned early if it was non-nil), `errors.As(err, &pgErr)` is **always false**. The pgError-specific handling (lines 90-96) is unreachable dead code — the intended "bridge Type conflict" generic message is never actually produced.

The real consequence is the opposite of what the question hypothesizes about a "shadow leaking DB details via the pgErr branch": instead, when `CreateBridgeType` actually returns a `pgconn.PgError` (e.g., due to a `external_initiators_name_key` unique-constraint violation from a race/duplicate bridge name), it is handled entirely at line 83-86, which calls `jsonAPIError(c, http.StatusInternalServerError, e)` with the **raw, unfiltered `e`**. `jsonAPIError` [4](#0-3)  does not redact non-`JSONAPIErrors` errors — it calls `err.Error()` directly, which for a `*pgconn.PgError` includes the raw Postgres message, detail, and constraint name (e.g., `ERROR: duplicate key value violates unique constraint "external_initiators_name_key" (SQLSTATE 23505)`).

So a caller with only the "edit" role (which per `router.go`'s role-based route wiring is sufficient to hit the Bridges create endpoint) can trigger a duplicate-name race or bypass the pre-check (`ValidateBridgeTypeNotExist`, which is a non-atomic check-then-insert and thus racy) and receive a `500` response containing raw Postgres constraint/table internals instead of the generic conflict message the code intended to return.

### Impact Explanation
This is an internal-error-message disclosure issue: an edit-role (non-admin) authenticated caller can receive verbatim Postgres error text (constraint name, SQLSTATE, potentially schema/table naming) in the API response body instead of the sanitized "bridge Type conflict" message the code was designed to produce. This maps to Chainlink's "information disclosure of internal implementation details" class — it does not expose secrets/keys or provide privilege escalation, but it deviates from the intended sanitization and leaks internal schema/constraint naming to a caller who should not see it.

### Likelihood Explanation
Reaching the constraint-violation path requires winning a race between concurrent bridge creation requests with the same name (since `ValidateBridgeTypeNotExist` performs a separate, non-transactional read before the `CreateBridgeType` insert), which is feasible for a caller who can send concurrent `POST /v2/bridge_types` requests with the same bridge name. No admin/operator access is required, only the ability to call the bridge-creation endpoint (edit role), which is a reachable, unprivileged (relative to admin) path.

### Recommendation
Fix the variable shadowing bug: capture and check the actual error from `CreateBridgeType`, not the stale `err` from `NewBridgeType`. E.g.:
```go
if err = orm.CreateBridgeType(ctx, bt); err != nil {
    var pgErr *pgconn.PgError
    if errors.As(err, &pgErr) && pgErr.ConstraintName == "external_initiators_name_key" {
        jsonAPIError(c, http.StatusConflict, fmt.Errorf("bridge Type %v conflict", bt.Name))
        return
    }
    jsonAPIError(c, http.StatusInternalServerError, err)
    return
}
```
Additionally, ensure all non-conflict `CreateBridgeType` errors are wrapped in a generic message rather than passing the raw driver error directly to `jsonAPIError`, to avoid leaking internal Postgres details in any code path.

### Proof of Concept
Go handler-level test plan (`core/web/bridge_types_controller_test.go`):
1. Create a mock `bridges.ORM` (or use the existing test app's ORM wrapper/mock) whose `CreateBridgeType` is stubbed/forced to return a `*pgconn.PgError{Code: "23505", ConstraintName: "external_initiators_name_key", Message: "duplicate key value violates unique constraint ..."}`.
2. Issue `POST /v2/bridge_types` as an edit-role client with a valid `BridgeTypeRequest` body.
3. Assert the HTTP status code returned is `500` (current buggy behavior) instead of the intended `409 Conflict`.
4. Assert the JSON response body's error detail contains the raw pgError string (constraint name / SQLSTATE) rather than the generic `"bridge Type %v conflict"` message — demonstrating both the dead-code bug (conflict branch never triggers) and the resulting leak of internal DB error text.
5. After applying the fix, re-run the same test and assert status `409` with only the generic sanitized message, and that constraint/schema text is absent from the response body.

### Citations

**File:** core/web/bridge_types_controller.go (L61-97)
```go
func (btc *BridgeTypesController) Create(c *gin.Context) {
	ctx := c.Request.Context()
	btr := &bridges.BridgeTypeRequest{}

	if err := c.ShouldBindJSON(btr); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}
	bta, bt, err := bridges.NewBridgeType(btr)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
	if e := ValidateBridgeType(btr); e != nil {
		jsonAPIError(c, http.StatusBadRequest, e)
		return
	}
	orm := btc.App.BridgeORM()
	if e := ValidateBridgeTypeNotExist(ctx, btr, orm); e != nil {
		jsonAPIError(c, http.StatusBadRequest, e)
		return
	}
	if e := orm.CreateBridgeType(ctx, bt); e != nil {
		jsonAPIError(c, http.StatusInternalServerError, e)
		return
	}
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		var apiErr error
		if pgErr.ConstraintName == "external_initiators_name_key" {
			apiErr = fmt.Errorf("bridge Type %v conflict", bt.Name)
		} else {
			apiErr = err
		}
		jsonAPIError(c, http.StatusConflict, apiErr)
		return
	}
```

**File:** core/web/helpers.go (L21-29)
```go
func jsonAPIError(c *gin.Context, statusCode int, err error) {
	_ = c.Error(err).SetType(gin.ErrorTypePublic)
	var jsonErr *models.JSONAPIErrors
	if errors.As(err, &jsonErr) {
		c.JSON(statusCode, jsonErr)
		return
	}
	c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))
}
```
