### Title
Whitelist bypass is not scoped per `policyId` — an entity exempted for one vault/realm is authorized on every vault sharing the `KeyringIdleWhitelist` - (File: contracts/KeyringIdleWhitelist.sol)

### Summary
`KeyringIdleWhitelist.checkCredential(policyId, entity)` returns `true` for any globally whitelisted entity regardless of which `policyId` the calling vault passes. This is the same bug class as CVE-2017-0910: an authorization granted in one realm (one credit vault / one Keyring policy) is silently honored in every other realm that delegates credential checks to the same whitelist contract, so a lender only meant to be exempted on one pool can deposit, request withdrawals and claim in any other pool whose Keyring policy it does not satisfy.

### Finding Description
`IdleCDOEpochVariant.isWalletAllowed` delegates the access decision to `IKeyring(keyring).checkCredential(keyringPolicyId, _user)`, where `keyring` and `keyringPolicyId` are per-vault parameters set via `setKeyringParams` [1](#0-0) . Every user-facing entry point funnels through this check: `_deposit` [2](#0-1) , `depositDuringEpoch` [3](#0-2) , and the queue's `requestDeposit`/`requestWithdraw` via `_checkAllowed` [4](#0-3) .

When `keyring` points at `KeyringIdleWhitelist`, the gate is:

```solidity
function checkCredential(uint256 policyId, address entity) external view returns(bool) {
  return whitelist[entity] || Keyring(keyring).checkCredential(policyId, entity);
}
```

`whitelist[entity]` is a single flat mapping with no `policyId` dimension [5](#0-4) . The admin whitelists an entity once (`setWhitelistStatus`) and that entity then satisfies **every** policy — the `policyId` argument is ignored on the whitelist path. Because `keyringPolicyId` exists precisely to give each vault its own credential realm, any vault that resolves `keyring` to the same `KeyringIdleWhitelist` instance (the contract is built as a shared wrapper around one upstream Keyring deployment, and `keyring` is even immutable after construction) shares the same bypass list. An entity exempted for pool A — e.g., an operational contract/market maker that cannot pass Keyring at all — is automatically a fully-authorized lender on pools B, C, … whose policies it was never evaluated against. Like CVE-2017-0910, membership in one realm mints standing access to all realms.

### Impact Explanation
The whitelist path is intended as an administrative escape hatch, but the missing policy scoping turns a single exemption into a cross-vault credential. Concretely, a non-KYC'd (or differently-credentialed) whitelisted entity can call `depositAA`/`depositBB`, `requestDeposit`, `requestWithdraw`, `depositDuringEpoch` and `claimWithdrawRequest` on a pool whose `keyringPolicyId` requires credentials it does not hold. Fund-wise it enters the tranche like any lender: it mints tranche tokens at `virtualPrice`, accrues a pro-rata share of `expectedEpochInterest`, and withdraws underlying — diluting compliant lenders' yield and letting an entity the pool's policy was configured to exclude route capital and extract interest through the vault. The loss is bounded by the pool's epoch interest on the attacker's deposit, but it is a direct, quantifiable transfer of yield to an unauthorized party, and the compliance gate it breaks is the only control separating realms.

### Likelihood Explanation
Triggering requires two vaults with different `keyringPolicyId`s sharing one `KeyringIdleWhitelist` instance, plus one whitelisted entity — a plausible configuration since the wrapper is designed around a single upstream Keyring address and per-vault instances would need to be deployed manually (I could not confirm the factory's deployment pattern within the search budget). The attacker is unprivileged (a whitelisted/KYC-passing lender in realm A), needs no privileged action beyond the honest admin's routine `setWhitelistStatus`, and can repeat the deposit/withdraw each running epoch. The defense-in-depth issue is that nothing in the code warns the admin that an exemption is global rather than per-policy.

### Recommendation
Scope the whitelist by the credential it replaces: `mapping(uint256 => mapping(address => bool)) public whitelist` keyed by `policyId`, and check `whitelist[policyId][entity]` in `checkCredential`. If a truly global bypass is desired for some entities, add an explicit `mapping(address => bool) public globalWhitelist` so the admin must opt into cross-realm authority deliberately, and emit both statuses in `Whitelist` events.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// Two credit-vault CDOs: vaultA (policyId 1) and vaultB (policyId 2),
// both initialized with keyring = address(keyringWhitelist).
// keyringWhitelist = new KeyringIdleWhitelist(realKeyring, admin);

// 1. Admin whitelists `attacker` intending it only for vaultA's realm.
vm.prank(admin);
keyringWhitelist.setWhitelistStatus(attacker, true);

// 2. Upstream Keyring would reject attacker under vaultB's policy.
// (realKeyring.checkCredential(2, attacker) == false)

// 3. checkCredential ignores policyId on the whitelist path:
assertTrue(keyringWhitelist.checkCredential(2, attacker)); // BUG: passes policy 2

// 4. Attacker deposits into vaultB during its buffer period and mints tranche tokens.
vm.startPrank(attacker);
underlying.approve(address(cdoB), amount);
uint256 minted = cdoB.depositAA(amount);   // should revert NotAllowed, succeeds

// 5. After startEpoch/stopEpoch, attacker requests withdraw and claims
//    underlying incl. its share of epoch interest — yield that vaultB's
//    policy was configured to deny it.
cdoB.requestWithdraw(minted, address(AAtrancheB));
// ... epoch rolls ...
cdoB.claimWithdrawRequest(); // receives underlying + interest
```

Key assertion: `keyringWhitelist.checkCredential(policyB, attacker) == true` while `realKeyring.checkCredential(policyB, attacker) == false`, and `cdoB.depositAA` succeeds for an entity that fails vaultB's own policy.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L644-649)
```text
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L656-669)
```text
  function depositDuringEpoch(uint256 _amount, address _tranche) external virtual returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L984-987)
```text
  function isWalletAllowed(address _user) public view returns (bool) {
    address _keyring = keyring;
    return _keyring == address(0) || IKeyring(_keyring).checkCredential(keyringPolicyId, _user);
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L415-421)
```text
  function _checkAllowed(address wallet) internal view {
    IdleCDOEpochVariant cdoEpoch = IdleCDOEpochVariant(idleCDOEpoch);
    if (!cdoEpoch.isEpochRunning()) {
      revert EpochNotRunning();
    }
    _checkNotAllowed(!cdoEpoch.isWalletAllowed(wallet));
  }
```

**File:** contracts/KeyringIdleWhitelist.sol (L78-80)
```text
  function checkCredential(uint256 policyId, address entity) external view returns(bool) {
    return whitelist[entity] || Keyring(keyring).checkCredential(policyId, entity);
  }
```
