### Title
Vault DKG result export endpoint requires only RequiresEditRole while equivalent key-export endpoints require RequiresAdminRole - (File: core/web/router.go, core/web/vault_controller.go)

### Finding Description
`VaultController.ExportDKGResult` in `core/web/vault_controller.go` reads the raw `ReportWithResultPackage` bytes for a given `instanceId` from the vault ORM and returns them hex-encoded directly to the caller via `presenters.NewExportDKGResultResource` [1](#0-0) . This handler is registered under `authv2.POST("/vault/dkg_results/export", ...)` in `core/web/router.go` wrapped only in `RequiresEditRole`, rather than `RequiresAdminRole` as used by the analogous `/v2/keys/eth/export` endpoint. Every other export-style endpoint that exposes key/cryptographic material in the router (ETH key export, CSA key export, OCR key bundle export, etc.) consistently requires the higher `RequiresAdminRole` tier, establishing an authorization pattern that export operations touching key/secret material are admin-only. The DKG result package returned here is the OCR report/result package produced by the DKG protocol, which is precisely the kind of sensitive cryptographic artifact the rest of the codebase treats as admin-gated when exported. Placing this specific export endpoint at the lower `RequiresEditRole` tier breaks that authorization consistency invariant, allowing any session with edit-role (a lower privilege than admin) to pull this artifact without needing admin-level access.

### Impact Explanation
An edit-role authenticated user — a lower-privileged role than admin — can call `POST /v2/vault/dkg_results/export` with a valid `instanceId` and receive the full hex-encoded DKG result package for that instance, material that the equivalent key-management export endpoints reserve for admins only. This is an authorization-tier inconsistency that exposes vault/DKG-related cryptographic material to a broader set of authenticated users than intended, corresponding to a credential/secret-disclosure impact class, scoped to nodes where non-admin "edit" accounts exist.

### Likelihood Explanation
The only precondition is possessing a valid edit-role session (an existing, unprivileged relative-to-admin credential) — no admin credentials, host access, or misconfiguration is required. The request is a straightforward single POST to a already-registered, always-reachable v2 route, making this trivially and repeatably exploitable by any edit-role user.

### Recommendation
Change the route registration for `/vault/dkg_results/export` (and re-evaluate `/vault/dkg_results/verify` if it also exposes decrypted material) in `core/web/router.go` to require `RequiresAdminRole`, matching the authorization tier used by all other key/secret export endpoints (e.g., `/v2/keys/eth/export`).

### Proof of Concept
1. In a `core/web/vault_controller_test.go`-style handler test, set up the app/router with an edit-role authenticated test client (mirroring existing edit-role fixtures used for other RequiresEditRole tests).
2. Seed a DKG result via the vault ORM (as done in `orm_test.go`) so `ReadResultPackage` returns a valid `ReportWithResultPackage`.
3. Issue `POST /v2/vault/dkg_results/export` with `{"instanceId": "<seeded-id>"}` using the edit-role client.
4. Assert the response is currently `200 OK` with the exported hex package (demonstrating the vulnerability), and add a regression assertion that after remediation (route requires `RequiresAdminRole`) the same request returns `403 Forbidden`, matching the behavior of an edit-role client hitting `/v2/keys/eth/export`.

### Citations

**File:** core/web/vault_controller.go (L104-119)
```go
	orm := vault.NewVaultORM(vc.App.GetDB())
	v, err := orm.ReadResultPackage(c.Request.Context(), dkgocrtypes.InstanceID(req.InstanceID))
	if err != nil {
		jsonAPIError(c, http.StatusNotFound, err)
		return
	}

	if v == nil {
		jsonAPIError(c, http.StatusNotFound, errors.New("DKG result not found"))
		return
	}

	hexPackage := hex.EncodeToString(v.ReportWithResultPackage)
	sha := sha256.Sum256(v.ReportWithResultPackage)
	shaStr := hex.EncodeToString(sha[:])
	jsonAPIResponse(c, presenters.NewExportDKGResultResource(hexPackage, shaStr), "exportDKGResult")
```
