### Title
KYC/Keyring revocation permanently blocks `requestWithdraw`, freezing a de-whitelisted lender's tranche funds - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The vault gates both entry (`_deposit`, `depositDuringEpoch`) and exit (`requestWithdraw`) behind the same `isWalletAllowed` Keyring credential check. This is the same flaw as the Homora report, where the token whitelist was applied symmetrically to borrow and repay: if a lender's credential is revoked after they deposited (e.g., regulatory/KYC expiry — an honest Keyring admin action), they can never submit a withdrawal request, so their principal plus accrued interest is locked in the vault indefinitely.

### Finding Description
`isWalletAllowed` checks `IKeyring(keyring).checkCredential(keyringPolicyId, _user)` at `IdleCDOEpochVariant.sol:984-987`. It is enforced in three places:

- `_deposit` at line 645 (entry — correct).
- `depositDuringEpoch` at line 668 (entry — correct).
- `requestWithdraw` at lines 741-744 (exit — the bug):

```solidity
_checkNotAllowed(
  (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
  !isWalletAllowed(msg.sender)
);
```

The downstream claim functions `claimWithdrawRequest` (line 967) and `claimInstantWithdrawRequest` (line 975) do **not** call `isWalletAllowed` — they forward `msg.sender` straight to `IdleCreditVault(strategy)`. So the protocol already treats claiming as KYC-exempt, but requesting is not, meaning a revoked wallet is stuck at exactly the step that converts tranche tokens into a funded receipt.

There is also no alternative escape path: `withdrawAA`/`withdrawBB` are not usable in the epoch variant (requests go through `requestWithdraw`), and `IdleCreditVault._transfer` (line 939-942) only lets the IdleCDO move strategy/receipt tokens. The only theoretical out is transferring `IdleCDOTranche` ERC20 tokens to a different KYC-passing wallet, which a revoked user generally cannot self-provision and which is not guaranteed to be possible for sanctioned addresses.

### Impact Explanation
A lender holding AA/BB tranche tokens whose Keyring credential lapses or is revoked loses all ability to exit: `requestWithdraw` reverts with `NotAllowed()` for every amount. Their principal and accrued epoch interest remain locked in the vault/borrower position for as long as the credential stays revoked — a direct analogue of "governance de-whitelists a token → users cannot repay/withdraw," producing temporary-to-permanent freezing of user funds with loss equal to the user's full tranche position (principal + interest).

### Likelihood Explanation
Credential revocation is a routine, honest administrative action (KYC expiry, policy change, jurisdiction blocking, sanctions screening). It requires no malicious privileged role — exactly like the governor de-whitelisting an unsafe token in the Homora report. Any lender with an active position at revocation time is affected, and the likelihood grows with the vault's lifetime and the number of lenders.

### Recommendation
Remove the `isWalletAllowed(msg.sender)` check from `requestWithdraw` (mirroring the already-unrestricted claim functions), or move the KYC gate so that burning tranche tokens to request a withdrawal is permissionless while deposits remain gated. If stricter control is desired, split permissions per operation (deposit vs. withdraw-request vs. claim) rather than reusing a single credential bit for both entry and exit.

### Proof of Concept
Foundry fork test sketch (requires reproducing the epoch-variant setup with a `keyring` address and `keyringPolicyId` configured):

```solidity
function testKycRevokedCannotRequestWithdraw() external {
    // buffer phase: KYC'd lender deposits into AA tranche
    keyring.setCredential(policyId, lender, true);
    vm.startPrank(lender);
    underlying.approve(address(cdo), amount);
    cdo.depositAA(amount);
    vm.stopPrank();

    // epoch lifecycle proceeds; epoch stops, withdraw requests reopen
    vm.prank(owner); cdo.startEpoch();
    vm.warp(epochEnd);
    vm.prank(owner); cdo.stopEpoch(interest);

    // honest Keyring admin revokes the lender's credential
    keyring.setCredential(policyId, lender, false);

    // lender can still claim pre-existing receipts, but can never request a withdrawal
    vm.startPrank(lender);
    vm.expectRevert(NotAllowed.selector);
    cdo.requestWithdraw(0, address(AAtranche)); // 0 = full balance
    vm.stopPrank();

    // tranche tokens (principal + interest) are locked indefinitely
    assertGt(AAtranche.balanceOf(lender), 0);
}
```

Uncertainty note: the PoC assumes the standard epoch-variant wiring where `keyring != address(0)`; if a deployment leaves `keyring` unset, `isWalletAllowed` always returns true and the issue is latent rather than active.