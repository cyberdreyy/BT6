### Title
Bridge creation leaks external-initiator name existence via unmasked Postgres constraint error - ([File: core/web/bridge_types_controller.go])

### Finding Description
`BridgeTypesController.Create` intends to mask collisions between a new bridge name and an existing external-initiator name behind a generic "conflict" message. The relevant code is: [1](#0-0) 

The bug is in control flow: when `orm.CreateBridgeType(ctx, bt)` fails (e.g. due to a unique-constraint violation referencing `external_initiators_name_key`), the function immediately returns the *raw* database error at line 84-86 (`jsonAPIError(c, http.StatusInternalServerError, e)`). The masking logic at lines 87-97, which checks `errors.As(err, &pgErr)` and rewrites `pgErr.ConstraintName == "external_initiators_name_key"` into a generic `"bridge Type %v conflict"` message, is dead code — it is only reached when `e == nil`, and it inspects the stale `err` variable from the earlier `bridges.NewBridgeType(btr)` call (line 69), not the actual `e` returned from `CreateBridgeType`. Since `err` was already confirmed `nil` at lines 70-73 before this point, `errors.As(nil, &pgErr)` never matches, so the intended masking branch is unreachable.

As a result, if bridge creation triggers a real Postgres unique-constraint violation tied to `external_initiators_name_key`, the raw `pgconn.PgError` (which includes the constraint name and typically a `DETAIL` field naming the conflicting value) is returned directly to the caller as a 500 response, instead of the intended generic 409 conflict message. This differs observably from the response for a non-colliding name (success) or a bridge-name-only collision (`ValidateBridgeTypeNotExist` at line 79, which returns a distinct "Bridge Type already exists" 400 message).

### Impact Explanation
An attacker with only enough privilege to reach `POST /v2/bridge_types` (edit-role or lower-privileged token per the stated precondition) can submit candidate names and observe response status/content to determine whether an external initiator with that name exists. This is a Chainlink "information disclosure / secret-confinement" class issue — the response cross-leaks the existence of a resource (external initiator) that the requester has no authorization to view, which is scoped intel useful for follow-up targeted credential/token replay against a specific external initiator name. The impact is limited to name-existence disclosure, not the initiator's credentials themselves.

### Likelihood Explanation
Preconditions require only the ability to call `POST /v2/bridge_types`, which is gated by the standard edit-role API authentication used throughout the bridges controller — no admin/owner access needed. The behavior is deterministic and repeatable: any request whose bridge name collides in a way that trips `external_initiators_name_key` will always hit the dead masking branch and return the raw DB error, while a non-colliding name will succeed or return the distinct "Bridge Type already exists" validation error. This makes automated enumeration straightforward.

### Recommendation
Fix the control flow so the Postgres error inspection operates on the actual error returned by `CreateBridgeType`, not the stale `err` from `NewBridgeType`, and perform the check before returning at lines 83-86:
```go
if e := orm.CreateBridgeType(ctx, bt); e != nil {
    var pgErr *pgconn.PgError
    if errors.As(e, &pgErr) && pgErr.ConstraintName == "external_initiators_name_key" {
        jsonAPIError(c, http.StatusConflict, fmt.Errorf("bridge Type %v conflict", bt.Name))
        return
    }
    jsonAPIError(c, http.StatusInternalServerError, e)
    return
}
```
Additionally, ensure the generic "conflict" message is used uniformly for both bridge-name collisions and external-initiator-name collisions to eliminate the response differential entirely (same status code and message text, no leakage of which table caused the conflict).

### Proof of Concept
Handler-level Go test plan for `core/web/bridge_types_controller_test.go`:
1. Seed an external initiator named `collide_name` via `BridgeORM/ORM.CreateExternalInitiator`.
2. Send `POST /v2/bridge_types` with `{"name": "collide_name", "url": "http://example.com"}` using a low-privileged edit-scoped session/token.
3. Assert current behavior: response is `500 Internal Server Error` with a body containing raw Postgres details (e.g. `"external_initiators_name_key"`, `duplicate key value violates unique constraint`) — demonstrating the leak.
4. As a control, send `POST /v2/bridge_types` with a name that does not collide with anything, and with a name that collides only with an existing bridge type (`ValidateBridgeTypeNotExist`), and assert the three responses are distinguishable (500 raw DB error vs 200 success vs 400 "Bridge Type already exists").
5. After applying the fix, re-run and assert both collision cases return an identical `409 Conflict` with the generic message, indistinguishable from each other.

### Citations

**File:** core/web/bridge_types_controller.go (L83-97)
```go
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
