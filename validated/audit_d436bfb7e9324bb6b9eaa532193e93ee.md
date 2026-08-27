### Title
Bridge deletion "jobs still using this bridge" check bypassable due to unnormalized bridge name lookup - ([File: core/web/bridge_types_controller.go])

### Finding Description
`BridgeTypesController.Destroy` reads the raw path parameter `name := c.Param("BridgeName")` and immediately normalizes it via `bridges.ParseBridgeName(name)` into `taskType`, which is then used for the existence check (`orm.FindBridge(ctx, taskType)`) and the actual deletion (`orm.DeleteBridgeType(ctx, &bt)`). [1](#0-0) 

However, the safety check that is supposed to prevent deleting a bridge that active jobs depend on calls `btc.App.JobORM().FindJobIDsWithBridge(ctx, name)` — using the **original, un-normalized** `name` variable instead of the normalized `taskType` that was used for `FindBridge`/`DeleteBridgeType`. [2](#0-1) 

This asymmetry means the identity used to look up "is this bridge referenced by any job" is not guaranteed to be the same identity used to look up and delete the bridge record itself. If a job's stored bridge reference does not exactly, byte-for-byte match the case/format of the path parameter supplied to `DELETE /v2/bridge_types/:BridgeName` (while still resolving to the same bridge via the ParseBridgeName-normalized lookup used by `FindBridge`), `FindJobIDsWithBridge` returns zero results even though a job that logically depends on that bridge exists. The 409 conflict guard at line 220 is then skipped, and `DeleteBridgeType` proceeds to remove a bridge that another user's job still depends on.

### Impact Explanation
An attacker holding bridge-delete privileges could delete a bridge that is depended upon by another user's active job by supplying a differently-cased/formatted variant of the bridge name in the DELETE request, while the actual job reference does not match that exact raw string. This breaks job/bridge referential integrity for a victim's job (unauthorized action affecting another user's resource) and opens a name-squatting window: the attacker (or anyone) can immediately recreate a bridge with the same normalized name, silently hijacking the victim job's future external-adapter callbacks/webhooks.

### Likelihood Explanation
Requires only the bridge-delete role (not admin/host access), consistent with the "unprivileged-but-role-holding" attacker model in scope. Exploitability depends on the exact normalization behavior of `bridges.ParseBridgeName` and how job pipeline specs store/reference bridge task names (i.e., whether a case/format mismatch between the DELETE path param and a job's stored bridge-name string can occur while both still resolve to the same canonical bridge via `FindBridge`). This could not be fully confirmed from the available source — the definition body of `ParseBridgeName`'s normalization rules and the internal implementation of `FindJobIDsWithBridge`'s matching logic were not retrievable in this session, so exact case-sensitivity of both sides is not verified end-to-end.

### Recommendation
Use the same normalized identifier (`taskType`, the output of `bridges.ParseBridgeName(name)`) for the `FindJobIDsWithBridge` call as is used for `FindBridge` and `DeleteBridgeType`, i.e., call `btc.App.JobORM().FindJobIDsWithBridge(ctx, taskType.String())` (or the equivalent typed value) instead of the raw `name` parameter, so the safety check and the deletion operate on identical normalized identity.

### Proof of Concept
Handler-level integration test plan:
1. Create a bridge with `ParseBridgeName`-normalized name `N` (e.g., created via `Create` so it's stored canonically).
2. Create/persist a job whose pipeline spec references the bridge using a string that normalizes to the same `N` but differs in raw form from the exact DB storage assumption of `FindJobIDsWithBridge`.
3. Call `Destroy` with `BridgeName` path param set to a case/format variant of `N` that still resolves via `ParseBridgeName`/`FindBridge` to the same bridge row.
4. Assert that today (pre-fix) `FindJobIDsWithBridge(ctx, name)` returns an empty slice (bypassing the check) and the handler proceeds to call `DeleteBridgeType`, returning 200 instead of the expected 409.
5. Apply the fix (pass `taskType` instead of `name`) and assert the same request now correctly triggers `http.StatusConflict` with the job ID listed, matching the intended "jobs still using this bridge" invariant.

### Citations

**File:** core/web/bridge_types_controller.go (L195-214)
```go
func (btc *BridgeTypesController) Destroy(c *gin.Context) {
	ctx := c.Request.Context()
	name := c.Param("BridgeName")

	taskType, err := bridges.ParseBridgeName(name)
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	orm := btc.App.BridgeORM()
	bt, err := orm.FindBridge(ctx, taskType)
	if errors.Is(err, sql.ErrNoRows) {
		jsonAPIError(c, http.StatusNotFound, errors.New("bridge not found"))
		return
	}
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, fmt.Errorf("error searching for bridge: %w", err))
		return
	}
```

**File:** core/web/bridge_types_controller.go (L215-223)
```go
	jobsUsingBridge, err := btc.App.JobORM().FindJobIDsWithBridge(ctx, name)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, fmt.Errorf("error searching for associated v2 jobs: %w", err))
		return
	}
	if len(jobsUsingBridge) > 0 {
		jsonAPIError(c, http.StatusConflict, fmt.Errorf("can't remove the bridge because jobs %v are associated with it", jobsUsingBridge))
		return
	}
```
