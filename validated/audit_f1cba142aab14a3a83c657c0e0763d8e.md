### Title
Bridge deletion guard uses unnormalized bridge name, allowing case-mismatch bypass of the in-use check - ([File: core/web/bridge_types_controller.go])

### Summary
`BridgeTypesController.Destroy` normalizes the bridge name via `bridges.ParseBridgeName` to look up and delete the bridge row itself, but passes the raw, un-normalized path parameter `name` to `JobORM().FindJobIDsWithBridge` when checking whether any job still depends on the bridge. Because bridge names are case-normalized when jobs reference them, a caller can delete a bridge that is still actively used by another job simply by changing the case of the bridge name in the DELETE request.

### Finding Description
In `Destroy`, the handler first normalizes the incoming name: [1](#0-0) 
`taskType` (the normalized form returned by `bridges.ParseBridgeName`) is used to find and later delete the actual `BridgeType` row via `orm.FindBridge(ctx, taskType)` and `orm.DeleteBridgeType(ctx, &bt)`.

However, the "is this bridge still referenced by a job" guard uses the raw, un-normalized `name` variable instead of `taskType`: [2](#0-1) 

Job specs store/reference bridge task types via the same normalized `bridges.BridgeName` form used at job-creation time (case-insensitive canonicalization), so a job created referencing `mybridge` stores the normalized name. If `FindJobIDsWithBridge` performs an exact-match comparison against that normalized, stored value, calling `DELETE /v2/bridge_types/MyBridge` (mixed case) will pass a differently-cased string that fails to match the stored `mybridge` reference. The guard then incorrectly reports zero jobs use the bridge, and `orm.FindBridge`/`orm.DeleteBridgeType` proceed to delete the bridge (since those use the correctly normalized `taskType`), even though a live job still depends on it.

This breaks the intended invariant that "deletion must be checked against the exact normalized entity used by job references" — the delete path and the in-use check operate on two different string representations of the same logical entity.

### Impact Explanation
An attacker with bridge-delete privileges (but no relationship to the specific job) can delete a bridge that another user's job actively depends on, bypassing the conflict/guard check that exists specifically to prevent this. Since bridge names are unique only up to normalization, the attacker can later recreate a bridge with the same (or original-case) name pointing to an attacker-controlled URL, causing the victim's job to silently route bridge task requests to attacker-supplied data — a cross-user response confusion / unauthorized state corruption impact.

### Likelihood Explanation
This requires only that the attacker hold the "bridge delete" API permission (no privilege over the specific job or its owner). The attack is fully attacker-triggerable via a single HTTP request (`DELETE /v2/bridge_types/:BridgeName`) with a case-varied bridge name, and is deterministic/repeatable given a target bridge name whose case can be guessed or observed (bridge names are typically visible via `Index`/job spec listings).

### Recommendation
Use the normalized `taskType.String()` (or equivalent canonical form) consistently for the job-reference guard, i.e., call `btc.App.JobORM().FindJobIDsWithBridge(ctx, taskType.String())` instead of the raw `name`, so the guard and the delete operate on the same normalized identity.

### Proof of Concept
1. Create a job (via `JobORM`) referencing a bridge named `mybridge` (lowercase), simulating normal job creation which stores the normalized bridge name.
2. Create the bridge type `mybridge` via `BridgeORM().CreateBridgeType`.
3. Call `BridgeTypesController.Destroy` (or issue `DELETE /v2/bridge_types/MyBridge`) using the mixed-case name `MyBridge`.
4. Assert expected behavior: the response should be `409 Conflict` (bridge still in use) — but under current code, if `FindJobIDsWithBridge` does exact/case-sensitive matching, the response will be `200 OK` and the bridge row will be deleted despite the still-existing job reference, demonstrating the bypass.
5. Add a corresponding fix verification test asserting that after passing `taskType.String()` to `FindJobIDsWithBridge`, the mixed-case delete request correctly returns `409 Conflict`.

### Citations

**File:** core/web/bridge_types_controller.go (L195-206)
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
