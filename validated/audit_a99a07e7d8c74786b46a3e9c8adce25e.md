### Title
Whitelisting an entity for one Keyring policy silently grants it KYC-bypass access to every vault sharing `KeyringIdleWhitelist` - (File: contracts/KeyringIdleWhitelist.sol)

### Summary
`KeyringIdleWhitelist.checkCredential` treats the admin-managed `whitelist` mapping as a global (farm-wide) credential: `return whitelist[entity] || Keyring(keyring).checkCredential(policyId, entity)`. The `policyId` parameter — which is what scopes a credential check to a specific vault's KYC policy — is ignored for whitelisted entities. This is the direct analog of the XWiki bug: a privilege intended for one restricted scope (one vault's `keyringPolicyId`, like a "closed subwiki") leaks to every consumer of the shared contract ("the farm").

### Finding Description
`IdleCDOEpochVariant.isWalletAllowed` gates every user-facing fund flow on `IKeyring(keyring).checkCredential(keyringPolicyId, _user)` — deposits (`_deposit`), mid-epoch deposits (`depositDuringEpoch`), withdraw requests, and the epoch queue (`IdleCDOEpochQueue._checkAllowed`). Each `IdleCDOEpochVariant` sets its own `keyringPolicyId`, so pools can have different KYC requirements even when they share the same `KeyringIdleWhitelist` contract (a standard deployment pattern for a protocol whitelist wrapper).

`KeyringIdleWhitelist.checkCredential` (contracts/KeyringIdleWhitelist.sol:78-80) ignores `policyId` entirely for the whitelist path:

```solidity
function checkCredential(uint256 policyId, address entity) external view returns(bool) {
    return whitelist[entity] || Keyring(keyring).checkCredential(policyId, entity);
}
```

The admin (honest per the threat model) whitelists an entity — e.g., a custody wallet, an integration contract, or an LP — so it can interact with vault A under `policyId = 1`. From that moment, `isWalletAllowed` returns `true` for that entity on *every* vault that uses the same `KeyringIdleWhitelist`, regardless of whether the entity could ever satisfy vault B's stricter `policyId = 2`. There is no per-policy scoping (`mapping(uint256 => mapping(address => bool))`), and no way for the admin to whitelist an entity for only one vault.

### Impact Explanation
Access-control invariant broken with direct fund impact. An attacker who is whitelisted for any single low-barrier vault (or who convinces an honest integration to be whitelisted) can, on a separate "closed" vault whose `keyringPolicyId` they cannot pass:

- call `_deposit`/`depositAA`/`depositBB` during the buffer and `depositDuringEpoch` mid-epoch (both gated only by `isWalletAllowed`, IdleCDOEpochVariant.sol:645, 668),
- hold tranche tokens and request withdrawals / instant withdrawals once the epoch flips,
- claim the epoch interest accrued on their position via `claimWithdrawRequest` / `claimInstantWithdrawRequest` (IdleCDOEpochVariant.sol:967-979), which route through `IdleCreditVault.claimWithdrawRequest` and pay out underlying with no additional credential check.

The theft is quantifiable: it equals the epoch interest (AA or BB per `trancheAPRSplitRatio`) and any instant-withdraw proceeds attributable to a position the vault's own access policy was configured to exclude — i.e., unclaimed yield is siphoned out of the pool by a wallet the policy was supposed to reject. This mirrors the advisory exactly: a "public" (whitelisted) identity on one scope grants access to a closed scope.

### Likelihood Explanation
Likelihood depends on deployment topology, which is a real configuration: `KeyringIdleWhitelist` is designed as a shared wrapper (`IdleCDOEpochVariant.setKeyringParams` points the vault at a keyring address plus a `keyringPolicyId`), so multiple vaults sharing one whitelist contract with different `keyringPolicyId`s is the natural deployment. The precondition is only that the honest admin whitelists any entity — the intended, routine use of the contract (`setWhitelistStatus` exists precisely for entities that can't pass Keyring). No privileged misbehavior is required; the bug is the missing `policyId` dimension in `whitelist`.

### Recommendation
Scope the whitelist by policy: change `whitelist` to `mapping(uint256 => mapping(address => bool))` and have `setWhitelistStatus` take a `policyId`, so `checkCredential(policyId, entity)` checks `whitelist[policyId][entity]`. Alternatively, deploy one `KeyringIdleWhitelist` per vault/policy and document that the whitelist must never be shared across policies. Add a regression test asserting that a whitelisted entity fails `checkCredential` for a different `policyId`.

### Proof of Concept
Foundry fork sketch:

```solidity
// Deploy vault A (policyId 1) and vault B (policyId 2), both with
// keyring = same KeyringIdleWhitelist instance; Keyring itself returns
// false for attacker under policyId 2.
KeyringIdleWhitelist wl = new KeyringIdleWhitelist(keyring, admin);

vm.prank(admin);
wl.setWhitelistStatus(attacker, true);          // intended for vault A only

vm.mockCall(keyring,
    abi.encodeWithSelector(IKeyring.checkCredential.selector, 1, attacker),
    abi.encode(true));
vm.mockCall(keyring,
    abi.encodeWithSelector(IKeyring.checkCredential.selector, 2, attacker),
    abi.encode(false));                          // attacker fails vault B policy

assertTrue(vaultB.isWalletAllowed(attacker));   // BUG: whitelist bypasses policyId 2

// attacker deposits into "closed" vault B during buffer, mints tranches,
// epoch runs, attacker requests + claims withdraw, receives underlying + yield
deal(underlying, attacker, 100_000e6);
vm.prank(attacker);
vaultB.depositAA(100_000e6);                    // succeeds, should revert
// ... startEpoch, advance epochDuration, stopEpoch, requestWithdraw,
// next epoch -> claimWithdrawRequest -> attacker.balanceOf increases by interest
```

Limitation: I did not verify whether `requestInstantWithdraw` in `IdleCDOEpochVariant` additionally re-checks `isWalletAllowed`; the whitelist-bypass deposit → epoch yield → withdraw-claim path is already sufficient for the fund impact regardless.