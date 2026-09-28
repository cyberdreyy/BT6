### Title
Lenders whose Keyring credential is revoked after depositing cannot create withdraw requests and have their funds frozen - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant` gates both the deposit path and the `requestWithdraw` path behind `isWalletAllowed(msg.sender)`, which delegates to an external Keyring credential check (`IKeyring(keyring).checkCredential(keyringPolicyId, user)`). A lender who deposits while KYC-valid and later loses their credential (expiry, off-chain revocation by the honest Keyring admin, or policy change) can no longer call `requestWithdraw`, permanently freezing their tranche position. This is the direct analog of the UXD report: a revocable whitelist controls exit, not just entry, so assets admitted under one whitelist state become stranded when the state flips.

### Finding Description
In `IdleCDOEpochVariant`:

- `_deposit` and `depositDuringEpoch` revert unless `isWalletAllowed(msg.sender)` (`contracts/IdleCDOEpochVariant.sol` ~L645, L668).
- `requestWithdraw` enforces the same check: the Foundry test `testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw` shows a wallet for which `checkCredential` returns false gets `NotAllowed()` on `cdoEpoch.requestWithdraw(...)` (`test/foundry/IdleCreditVaultWriteOffEscrow.t.sol` L288-291).
- `isWalletAllowed` resolves to `IKeyring(keyring).checkCredential(keyringPolicyId, _user)` whenever `keyring != 0` (`contracts/IdleCDOEpochVariant.sol` L984-987). Credentials are inherently revocable/expirable off-chain; the honest Keyring admin is not required to be malicious, merely to revoke or let a credential lapse, mirroring the report's "owner removes asset from whitelist for some arbitrary reason".

The sequence:

1. Alice passes KYC, deposits into AA tranche, receives `IdleCDOTranche` tokens (epoch buffer phase).
2. Alice's Keyring credential is revoked or expires off-chain.
3. Epoch runs/stops; Alice calls `requestWithdraw(amount, AAtranche)` → reverts `NotAllowed`.
4. Every subsequent epoch repeats the revert. Her tranche tokens cannot be burned for underlying through the normal path.

Mitigating exits exist but are unreliable: `claimWithdrawRequest`/`claimInstantWithdrawRequest` do not check `isWalletAllowed` (they only help already-open requests), tranche tokens may be transferable to another KYC'd wallet (only works if the user controls or trusts a second credentialed identity), and `WriteOffEscrow.fullfillWriteOffRequest` lets a non-KYC'd party sell tranches but requires a counterparty willing to buy during an epoch. None guarantees recovery, so the freeze is effective and can be permanent for a sole-identity user.

### Impact Explanation
Permanent freezing of user funds (tranche tokens that cannot be redeemed for underlying) for any lender whose Keyring credential is revoked between deposit and withdraw request. Loss = the lender's full position value minus whatever a secondary buyer might pay, which can be zero if no counterparty exists. In a default/emergency state the position additionally suffers loss socialization while the user remains unable to exit.

### Likelihood Explanation
KYC credential revocation is a routine, expected event (credential TTL expiry, sanctions/offboarding, policy-id rotation via `setKeyringParams`). Every affected depositor is impacted deterministically once revocation lands, and the revert is unconditional. No privileged malice is required — only the honest Keyring/owner acting as designed. That said, revocation must actually occur for a user holding a position, so likelihood is moderate rather than certain.

### Recommendation
Apply the whitelist check only to value-increasing actions (deposits, new exposure) and allow exits regardless of current credential status — i.e., drop the `isWalletAllowed` gate on `requestWithdraw` (and the epoch-queue equivalent `_checkAllowed` in `contracts/IdleCDOEpochQueue.sol` L415-421), matching the report's recommendation to permit withdrawals of non-whitelisted assets. Alternatively, let a revoked-credential user still request withdraw while routing the payout through a fresh KYC'd `receiver` address.

### Proof of Concept
The existing test already demonstrates the revert; a minimal Foundry PoC (mainnet fork or unit):

```solidity
// In an IdleCreditVault test context (mirroring testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw)
function testKycRevokedLenderCannotRequestWithdraw() external {
    address user = makeAddr("user");
    address keyring = address(1);

    // KYC passes at deposit time
    vm.prank(cdoEpoch.owner());
    cdoEpoch.setKeyringParams(keyring, 1);
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector), abi.encode(true));

    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(user, amount, true);              // mint AA tranche tokens
    uint256 trancheBal = IERC20(AAtranche).balanceOf(user);

    // Credential revoked / expired while position is open
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector), abi.encode(false));

    // Any subsequent requestWithdraw reverts -> position frozen
    _stopCurrentEpoch(); // or buffer phase; gate applies either way
    vm.prank(user);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.requestWithdraw(trancheBal, address(AAtranche));

    assertEq(cdoEpoch.isWalletAllowed(user), false);
    assertGt(trancheBal, 0, "frozen tranche position");
}
```

Note on residual uncertainty: I confirmed the `requestWithdraw` KYC gate via the in-repo test that asserts the revert and via `_checkAllowed` in the epoch queue; I did not re-read the exact line inside `requestWithdraw` in `IdleCDOEpochVariant.sol`, but the test assertion is unambiguous evidence the check exists on that path.