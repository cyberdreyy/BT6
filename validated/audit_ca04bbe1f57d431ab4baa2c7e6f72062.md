### Title
Run-role authenticated users can export raw CSA private key material via unguarded `/v2/keys/csa/export/:ID` endpoint - ([File: core/web/csa_keys_controller.go])

### Finding Description
`CSAKeysController.Export` at [1](#0-0)  takes the `ID` route parameter and `newpassword` query parameter directly from the request, calls `ctrl.App.GetKeyStore().CSA().Export(keyID, newPassword)`, and writes the raw encrypted key bytes back to the client via `c.Data(http.StatusOK, MediaType, bytes)` with no ownership check, no role check, and no filtering of which CSA key ID may be requested. The handler performs no authorization decision of its own — any access control must come from route-level middleware in `core/web/router.go`.

Searching the codebase for role-enforcement primitives (`RequiresEditRole`) shows they are only referenced in `core/web/auth/auth.go` and `core/web/user_controller.go` — i.e., used for user-role management endpoints — and are not applied to the `/v2/keys/csa/*` route group registered in `core/web/router.go`. This means the CSA key routes (`Index`, `Create`, `Import`, `Export`) sit behind generic session/token authentication only, with no differentiation between `admin`, `edit`, and `run` roles. CSA keys are cluster-wide identity credentials (not per-user, not scoped to job "owners"), so there is no notion of a resource-owner check either — any authenticated identity, regardless of role, can request export of any CSA key ID that exists on the node.

### Impact Explanation
This matches Chainlink's "key/secret disclosure" bounty impact class: an unprivileged, low-role authenticated identity (run-role user/API token) can retrieve the raw encrypted private key material of a CSA key without needing edit/admin privileges, undermining the intended privilege separation between roles. The key is still password-encrypted (the export flow always requires supplying an export password), so the immediate blast radius depends on how well the exported blob is protected, but the "secrets never leave" invariant for non-admin identities is violated regardless — export capability itself is meant to be an administrative operation.

### Likelihood Explanation
Preconditions: attacker needs any valid, currently-authenticated session or API token on the node — no admin/edit privilege is required, since the route registration does not gate on role, only on general authentication. This is trivially reproducible by any operator that provisions a lower-privileged ("run" role) account for automation/monitoring purposes, which is a common and encouraged Chainlink deployment practice.

### Recommendation
Wrap the `/v2/keys/csa` route group (and other key-management routes: `Create`, `Import`, `Export`, `Delete`) with the existing `RequiresEditRole`/admin-role middleware used elsewhere (e.g., `core/web/auth/auth.go`), so only `admin` (or at minimum `edit`) role sessions/tokens can invoke key export/import/create operations. `run`-role tokens should be limited to read-only or job-triggering endpoints only.

### Proof of Concept
1. In a handler-level integration test (using `cltest` helpers similar to `core/web/csa_keys_controller_test.go` if present, or `core/internal/cltest`), create two authenticated clients: one with `admin` role and one with `run` role.
2. As `admin`, call `POST /v2/keys/csa` to create a CSA key, capturing its ID.
3. As `run`-role client, call `POST /v2/keys/csa/export/:ID?newpassword=x` with the captured ID.
4. Assert current (vulnerable) behavior: HTTP 200 and response body contains the exported encrypted key JSON — expected/fixed behavior: HTTP 403 and no key bytes in body.
5. Repeat assertion for `Import`/`Create`/`Delete` to confirm the same missing role gate.

### Citations

**File:** core/web/csa_keys_controller.go (L83-97)
```go
func (ctrl *CSAKeysController) Export(c *gin.Context) {
	defer ctrl.App.GetLogger().ErrorIfFn(c.Request.Body.Close, "Error closing Export request body")

	keyID := c.Param("ID")
	newPassword := c.Query("newpassword")

	bytes, err := ctrl.App.GetKeyStore().CSA().Export(keyID, newPassword)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	ctrl.App.GetAuditLogger().Audit(audit.CSAKeyExported, map[string]any{"keyID": keyID})
	c.Data(http.StatusOK, MediaType, bytes)
}
```
