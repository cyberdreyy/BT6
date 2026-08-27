Based on my investigation, I found a valid analog. The vulnerability class from the report ("changing a threshold/membership configuration leads to a stale cached security decision that is enforced instead of the current one") maps onto the `zoneBRestrictor` cache in the Vault capability.

### Title
Stale zone-b DON membership cache can bypass the vault secrets allowlist restriction during a capabilities-registry hiccup after a DON is reassigned into the restricted zone - ([File: core/capabilities/vault/zone_b_restriction.go])

### Summary
`zoneBRestrictor` caches, per `WorkflowDonID`, whether that DON belongs to the restricted `zone-b` family [1](#0-0) . Like the `ERC20ConvictionScore.isGovernance` cache that goes stale after `governanceThreshold` changes, this DON-membership boolean is only refreshed on a successful registry lookup and otherwise a prior (potentially outdated) value is used to make the current security decision [2](#0-1) .

### Finding Description
`enforce()` is invoked on every `vault.Capability.Execute` call for `GetSecrets`, before the request is allowed to proceed [3](#0-2) . It calls `isZoneBWorkflowDON`, which queries `capabilitiesRegistry.DONByID` to authoritatively resolve whether the calling `WorkflowDonID` belongs to the `zone-b` family, and only then applies the owner allowlist gate (`ownerAllowed.AllowErr`) [4](#0-3) .

If the DON was previously **not** part of `zone-b` (e.g. `zone-a`) and its membership is later changed to `zone-b` (a DON-family reassignment, analogous to the FSD `updateGovernanceThreshold` config change), the on-disk boolean cached by `storeZoneMembership` still holds the old `false` value [5](#0-4) . If, in that window, a transient registry read failure occurs (explicitly acknowledged in the code comments as expected/normal: "not yet synced after startup, or nil mid-update") [6](#0-5) , `isZoneBWorkflowDON` falls back to the stale cached value instead of failing closed:

```go
if cached, ok := z.cachedZoneMembership(workflowDonID); ok {
    z.lggr.Warnw("capabilities registry lookup failed; using cached zone-b membership", ...)
    return cached, nil
}
```

Because the stale value is `false` (not-zone-b), `enforce()` returns `nil` and the whole `ownerAllowed` allowlist check is skipped entirely, letting a non-allowlisted workflow owner successfully call `GetSecrets` on a DON that has since been designated restricted. This is the same bug class as the report: a configuration/membership change is not atomically reflected in a per-entity cached boolean used to gate a security decision, and a subsequent (even benign) trigger — a wei transfer in FSD, a registry hiccup here — causes the stale cached state to be applied, producing an outcome inconsistent with the current, intended configuration.

The existing regression test `TestZoneBRestrictor_RegistryOutageUsesCachedMembership` only validates the two *stable* scenarios (zone-a stays zone-a, zone-b stays zone-b across an outage) [7](#0-6) ; it does not cover the transition case where a DON's family assignment changes between the last successful resolution and the next lookup attempt, which is exactly the scenario that reproduces the bypass.

### Impact Explanation
A workflow owner not on the zone-b allowlist could read vault-managed secrets for a DON that has been (re)assigned to the restricted `zone-b` family, if a capabilities-registry read for that DON fails at least once after the reassignment and before the cache is refreshed. This is a concrete allowlist bypass in a secret-access control path, matching the "allowlist bypass" / "key/secret disclosure" criteria.

### Likelihood Explanation
This requires DON family membership to change (an operator/administrative action) coinciding with a capabilities registry lookup failure for that specific DON, which the code's own comments describe as a normal/expected occurrence ("not yet synced after startup, or nil mid-update"). It does not require a malicious or compromised node — it is a race intrinsic to normal node operation, making it plausible though timing-dependent (moderate likelihood, medium severity, matching the "Medium" severity of the analog report).

### Recommendation
Do not use the cached membership to widen the effective allowlist scope for a request in ambiguous or currently-unresolvable states. Specifically:
- On registry lookup failure, fail closed (treat as `zone-b`/restricted) rather than falling back to a "not restricted" cached value, or
- Separately track "last known family set" with a freshness/TTL bound and refuse to serve any decision derived from data staler than a defined threshold, or
- Invalidate/re-resolve the cache proactively on any DON-family/topology change notification, rather than only lazily on the next successful lookup.

### Proof of Concept
1. Register `WorkflowDonID = D` in family `zone-a` in the capabilities registry; the restrictor caches `zoneCache[D] = false` on the first `GetSecrets` call routed through `enforce`.
2. An operator reassigns `D` to family `zone-b` on-chain/in the registry (the security-relevant config change).
3. Before the node's local registry view is fully re-synced (a transient state acknowledged by the code comments), a workflow owned by a non-allowlisted address issues a `GetSecrets` request from DON `D`.
4. `capabilitiesRegistry.DONByID(D)` returns an error (e.g., "metadataRegistry information not available") during this window.
5. `isZoneBWorkflowDON` falls back to `cachedZoneMembership(D)`, which still returns `false`.
6. `enforce()` treats the caller as non-zone-b and returns `nil`, skipping the `ownerAllowed.AllowErr` check entirely — the non-allowlisted request proceeds to `handleRequest` and secrets are returned. [8](#0-7)

### Citations

**File:** core/capabilities/vault/zone_b_restriction.go (L36-43)
```go
	// zoneCacheMu guards zoneCache.
	zoneCacheMu sync.RWMutex
	// zoneCache holds the last successfully-resolved zone-b membership per
	// WorkflowDonID. It is the fallback when the capabilities registry view is
	// transiently unavailable, so a registry blip does not fail every vault read
	// DON-wide (see isZoneBWorkflowDON).
	zoneCache map[uint32]bool
}
```

**File:** core/capabilities/vault/zone_b_restriction.go (L66-94)
```go
// enforce denies GetSecrets reads originating from a zone-b workflow DON unless
// the calling workflow owner is allowlisted. It is a no-op unless the master
// gate (VaultZoneBWorkflowGetSecretsRestrictEnabled) is open and the caller
// resolves to a zone-b DON. The owner is read from ctx, which must already carry
// the (normalized) CRE owner via RequestMetadata.ContextWithCRE.
func (z *zoneBRestrictor) enforce(ctx context.Context, workflowDonID uint32) error {
	enabled, err := z.restrictEnabled.Limit(ctx)
	if err != nil {
		return fmt.Errorf("could not evaluate zone-b vault read restriction gate: %w", err)
	}
	if !enabled {
		return nil
	}

	isZoneB, err := z.isZoneBWorkflowDON(ctx, workflowDonID)
	if err != nil {
		// Fail closed: if we cannot authoritatively resolve the caller's zone, do
		// not proceed. The registry is in-process, so this only fires for an
		// unknown/unregistered WorkflowDonID.
		return err
	}
	if !isZoneB {
		return nil
	}

	if err := z.ownerAllowed.AllowErr(ctx); err != nil {
		return fmt.Errorf("zone-b workflow DON may only read vault secrets for allowlisted workflow owners: %w", err)
	}
	return nil
```

**File:** core/capabilities/vault/zone_b_restriction.go (L100-122)
```go
func (z *zoneBRestrictor) isZoneBWorkflowDON(ctx context.Context, workflowDonID uint32) (bool, error) {
	don, err := z.capabilitiesRegistry.DONByID(ctx, workflowDonID)
	if err != nil {
		// The registry view can be transiently unavailable (e.g. not yet synced
		// after startup, or nil mid-update: DONByID returns "metadataRegistry
		// information not available"). That error is not specific to zone-b
		// callers, so failing closed here would block every vault GetSecrets read
		// DON-wide. Fall back to the last successfully-resolved membership for this
		// DON; only a never-before-resolved DON fails closed.
		if cached, ok := z.cachedZoneMembership(workflowDonID); ok {
			z.lggr.Warnw("capabilities registry lookup failed; using cached zone-b membership",
				"workflowDonID", workflowDonID, "isZoneB", cached, "err", err)
			return cached, nil
		}
		return false, fmt.Errorf("could not resolve caller workflow DON %d for zone-b vault read restriction: %w", workflowDonID, err)
	}
	// Case-insensitive match: family casing may vary across registry sources.
	isZoneB := slices.ContainsFunc(don.Families, func(family string) bool {
		return strings.EqualFold(family, zoneBFamily)
	})
	z.storeZoneMembership(workflowDonID, isZoneB)
	return isZoneB, nil
}
```

**File:** core/capabilities/vault/capability.go (L107-112)
```go
	// Reject GetSecrets reads from a restricted zone-b workflow DON before the
	// request enters the OCR queue, so non-allowlisted owners get a deterministic
	// rejection and zone-a / gateway paths are unaffected.
	if err := s.zoneBRestrictor.enforce(ctx, request.Metadata.WorkflowDonID); err != nil {
		return capabilities.CapabilityResponse{}, err
	}
```

**File:** core/capabilities/vault/zone_b_restriction_test.go (L251-284)
```go
func TestZoneBRestrictor_RegistryOutageUsesCachedMembership(t *testing.T) {
	t.Parallel()
	const restrictEnabled = `{"global":{"VaultZoneBWorkflowGetSecretsRestrictEnabled":"true"}}`

	t.Run("cached zone-a membership survives outage (still allowed)", func(t *testing.T) {
		t.Parallel()
		z, fake := newOutageTestRestrictor(t, restrictEnabled)
		md := capabilities.RequestMetadata{WorkflowOwner: "0xdeadbeef", WorkflowDonID: zoneADonID}
		ctx := md.ContextWithCRE(t.Context())

		// Warm the cache while healthy: zone-a caller is allowed.
		require.NoError(t, z.enforce(ctx, zoneADonID))
		// Outage: cached (non-zone-b) membership keeps the read allowed.
		fake.fail.Store(true)
		require.NoError(t, z.enforce(ctx, zoneADonID))
	})

	t.Run("cached zone-b membership survives outage (still denied)", func(t *testing.T) {
		t.Parallel()
		z, fake := newOutageTestRestrictor(t, restrictEnabled)
		md := capabilities.RequestMetadata{WorkflowOwner: "0x" + allowlistedOwner, WorkflowDonID: zoneBDonID}
		ctx := md.ContextWithCRE(t.Context())

		// Warm the cache while healthy: zone-b + non-allowlisted owner is denied,
		// and the zone-b membership is cached.
		require.ErrorContains(t, z.enforce(ctx, zoneBDonID), "allowlisted workflow owners")

		// Outage: the cached zone-b membership must still be enforced. The caller
		// is denied via the allowlist, not allowed through by a resolve failure.
		fake.fail.Store(true)
		err := z.enforce(ctx, zoneBDonID)
		require.ErrorContains(t, err, "allowlisted workflow owners")
		require.NotContains(t, err.Error(), "could not resolve caller workflow DON")
	})
```
