### Title
DKG vault key material exportable by edit-role (non-admin) users via under-privileged route guard - ([File: core/web/router.go])

### Summary
The `/v2/vault/dkg_results/export` route is registered with `auth.RequiresEditRole(vault.ExportDKGResult)` instead of `auth.RequiresAdminRole`, so any authenticated session/token holding the Edit role (not Admin) can call `VaultController.ExportDKGResult` and receive the raw hex-encoded DKG result package and its SHA-256 digest. That package is the same secret material used by `vaultcap.VerifyDKGResult` to derive the node's TDH2 master secret key share, i.e. vault/DKG key material that should be admin-only.

### Finding Description
`VaultController.ExportDKGResult` (`core/web/vault_controller.go:91-119`) takes an `instanceId`, reads the stored DKG result package via `vault.NewVaultORM(vc.App.GetDB()).ReadResultPackage`, and returns `hex.EncodeToString(v.ReportWithResultPackage)` plus its checksum through `presenters.NewExportDKGResultResource`. `v.ReportWithResultPackage` is the same byte blob that `VerifyDKGResult` (`core/capabilities/vault/verify.go:13-45`) unmarshals to derive `MasterSecretKeyShare(key)` — i.e. it embeds the encrypted DKG/vault key share material for the DON's threshold key.

In `core/web/router.go:441-443`, both `/vault/dkg_results/verify` and `/vault/dkg_results/export` are wrapped only with `auth.RequiresEditRole`, alongside other operationally-scoped write endpoints (job spec deletion, forwarder tracking), while genuinely sensitive operations elsewhere in the router (e.g. `LogController.Patch` at line 412) are wrapped with `auth.RequiresAdminRole`. There is no additional secret-redaction step in `presenters.NewExportDKGResultResource` and no capability/ownership check tying the exported package to the requester — any principal satisfying `RequiresEditRole` (Edit or Admin role) can retrieve the full package for any `instanceId` they can guess or enumerate.

### Impact Explanation
This is a key/secret disclosure to an insufficiently-privileged principal: an Edit-role session or API token — which is meant for job/spec management, not custody of DKG threshold key shares — can pull raw vault/DKG result packages out of the node. Depending on downstream use of these shares (participating with other captured/colluding nodes' shares to approach the DKG reconstruction threshold, or replaying/verifying against the master public key), this weakens the confidentiality guarantee of the vault's threshold cryptosystem, which is exactly the "minimum-role-per-sensitivity" invariant the endpoint is supposed to enforce.

### Likelihood Explanation
Minimal precondition: possession of a valid Edit-role session cookie or API token (not Admin) is sufficient — no special network position, no OCR/DON compromise, no misconfiguration beyond the route wrapper itself. The attack is a single `POST /v2/vault/dkg_results/export` with a known/enumerable `instanceId`, fully reproducible via `httptest`.

### Recommendation
Change `core/web/router.go:443` (and arguably line 442 for `verify` too, since it also operates on the same secret package) from `auth.RequiresEditRole` to `auth.RequiresAdminRole`, matching the admin-only handling intended for vault/DKG key material.

### Proof of Concept
1. In `core/web/vault_controller_test.go`, add a test that creates an app/router with a DKG result package seeded via the vault ORM.
2. Authenticate an HTTP client as a user/token with `sessions.UserRoleEdit` (not Admin).
3. Issue `POST /v2/vault/dkg_results/export` with `{"instanceId": "<seeded-id>"}`.
4. Assert current behavior: HTTP 200 with a response body containing the hex-encoded `ReportWithResultPackage` and sha256 (proving disclosure).
5. Assert expected/fixed behavior: after changing the route to `auth.RequiresAdminRole`, the same Edit-role request returns 401/403 and the body does not contain the package data; only an Admin-role session/token succeeds.