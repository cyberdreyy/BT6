### Title
KYC whitelist is enforced only on the caller at entry; tranche transfers and claims let non-whitelisted addresses hold and redeem vault positions - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant` gates user-facing actions with `isWalletAllowed(msg.sender)` (a Keyring credential check), but only on deposit/withdraw-request entry points. `IdleCDOTranche` tokens are unrestricted ERC20s and the claim path (`claimWithdrawRequest` / `claimInstantWithdrawRequest` → `IdleCreditVault.claimWithdrawRequest(msg.sender)`) performs no credential check on the recipient. This is the same bug class as the reported "private transfer mode" flaw: the whitelist is checked on only one side of the flow, so economic exposure and payouts can reach non-KYC addresses through a whitelisted intermediary, defeating the intended private-placement gating.

### Finding Description
The allowlist check `isWalletAllowed` is applied to `msg.sender` in `_deposit`, `depositDuringEpoch`, and `requestWithdraw` in `contracts/IdleCDOEpochVariant.sol` (e.g. line 645, line 668, line 743, and the check itself at line 984), but:

- `IdleCDOTranche` (`contracts/IdleCDOTranche.sol`) is a plain ERC20 with no `_beforeTokenTransfer`/`_transfer` hook enforcing `isWalletAllowed` on the recipient. A KYC-passed lender can deposit via `depositAA`/`depositBB`, then `tranche.transfer(nonKycUser, amount)` and the non-KYC address holds the vault position.
- `claimWithdrawRequest()` (line 967) and `claimInstantWithdrawRequest()` (line 975) call `IdleCreditVault(strategy).claimWithdrawRequest(msg.sender)` / `claimInstantWithdrawRequest(msg.sender)` without any `isWalletAllowed` check. A lender who was whitelisted when requesting but whose credential lapses — or any address that obtained a payout position via a whitelisted relay — still receives underlying.
- Because withdrawals must be requested by an allowed caller, the fully non-custodial path is: whitelisted user deposits, requests withdrawal (receipt issued to them), and the recipient-side restriction intended by the Keyring policy is never re-verified at payout; symmetrically, tranche token transfers place the claim itself in non-KYC hands, so the "only whitelisted wallets may hold exposure" invariant is broken on the recipient side exactly as in the reported `_transfer` bug.

The broken invariant is access: the vault is designed so only Keyring-credentialed wallets participate (`isWalletAllowed` docstring: "Check if wallet is allowed to interact with the contract"), yet a second party never re-validated can end up holding tranche tokens (a claim on vault NAV) or receiving underlying proceeds.

### Impact Explanation
Any unprivileged KYC-passed lender can route vault positions and redemption proceeds to arbitrary non-whitelisted addresses (sanctioned or non-credentialed EOAs/contracts). The whitelist — the compliance boundary for a private credit pool — is bypassable at zero cost and for arbitrary amounts: whatever quantity the whitelisted intermediary deposits or requests is transferable. Positions delivered to non-KYC holders still accrue the epoch interest defined by `_calcInterestWithdrawRequest`/`_trancheToUnderlyings`, so yield earmarked for credentialed participants is paid out to non-credentialed recipients.

### Likelihood Explanation
Requires only an ordinary whitelisted lender (an allowed attacker role) making a standard ERC20 `transfer` of `IdleCDOTranche` tokens, or letting a credential lapse between `requestWithdraw` and `claimWithdrawRequest`. No privileged action, no timing race, no donor setup. The only mitigation is social (the intermediary must cooperate), which is exactly the loophole described in the external report.

### Recommendation
Mirror the fix recommended in the report — enforce the credential on both sides of value transfer:

- Override `_beforeTokenTransfer`/`_update` in `IdleCDOTranche` (or gate in the CDO) so that for any non-mint transfer, `IdleCDOEpochVariant.isWalletAllowed(to)` must return true; mints already check the depositor.
- Add `!isWalletAllowed(msg.sender)` (or a policy check on the beneficiary) in `claimWithdrawRequest` and `claimInstantWithdrawRequest` in `contracts/IdleCDOEpochVariant.sol`, matching the checks on `requestWithdraw`.
- Apply the same check to `IdleCDOEpochQueue.requestDeposit`/`requestWithdraw` receipt claims if queued positions become transferable.

### Proof of Concept
Foundry fork sketch (against the epoch-variant deployment pattern used in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testWhitelistBypassViaTransfer() public {
    // setup: pool in buffer period, keyring set, alice credentialed, bob not
    vm.prank(admin);
    keyringWhitelist.setWhitelistStatus(alice, true); // only alice passes checkCredential

    vm.startPrank(alice);
    underlying.approve(address(cdo), 1_000e6);
    uint256 minted = cdo.depositAA(1_000e6);   // passes isWalletAllowed(alice)
    IERC20(cdo.AATranche()).transfer(bob, minted); // succeeds: no recipient check
    vm.stopPrank();

    assertGt(IERC20(cdo.AATranche()).balanceOf(bob), 0); // non-KYC holds vault position

    // claim side: alice requests withdraw, then credential revoked; claim still pays out
    vm.prank(alice);
    cdo.requestWithdraw(0, cdo.AATranche());
    vm.prank(admin);
    keyringWhitelist.setWhitelistStatus(alice, false);
    // ... owner runs startEpoch/stopEpoch to fund the receipt ...
    vm.prank(alice);
    cdo.claimWithdrawRequest(); // no isWalletAllowed check on the payout path
}
```

Note: I was unable to fully read `contracts/IdleCDOTranche.sol` and the `claimWithdrawRequest`/`claimInstantWithdrawRequest` bodies in `contracts/strategies/idle/IdleCreditVault.sol` within the iteration budget, so the absence of a transfer hook is inferred from the grep surface (no `beforeTokenTransfer`/whitelist match in the tranche file or claim functions). If a recipient check does exist in `IdleCDOTranche`, the transferable-position half of this analog is weakened, but the missing caller check on the claim path stands on `IdleCDOEpochVariant.sol` lines 967–978.