### Title
KYC whitelist on `IdleCDOEpochVariant` deposits is bypassed because `IdleCDOTranche` tokens are freely transferable - ([File: contracts/IdleCDOTranche.sol](contracts/IdleCDOTranche.sol))

### Summary
`IdleCDOEpochVariant` enforces Keyring/KYC whitelisting (`isWalletAllowed` via `KeyringIdleWhitelist.checkCredential`) on `depositAA`/`depositBB`, but the `IdleCDOTranche` receipt tokens are plain ERC20 with no transfer hook. A whitelisted lender can deposit and transfer tranche tokens to a non-whitelisted address, which then accrues interest and exits via `requestWithdraw`/`claimWithdrawRequest` — flows that never re-check the credential. This mirrors the StakeWise `EthPrivErc20Vault` bug where `transfer`/`transferFrom` bypass the deposit whitelist.

### Finding Description
`contracts/KeyringIdleWhitelist.sol:78` is only consulted on the deposit path in `IdleCDOEpochVariant`/`IdleCDO`, where `msg.sender` (and/or receiver) must satisfy `checkCredential`. The tranche token at `contracts/IdleCDOTranche.sol:7` is a bare `ERC20` — mint/burn are gated to the CDO (`minter`), but `transfer`/`transferFrom` are unrestricted. Meanwhile `IdleCreditVault.requestWithdraw` (`contracts/strategies/idle/IdleCreditVault.sol:243`) mints a receipt `_mint(_user, _amount)` bound to the calling user, and `claimWithdrawRequest` (line 301) / `_transferFundedClaim` (line 897) send underlying to that user — keyed purely on tranche/receipt balance and per-epoch request state, with no credential check. The same applies to `requestInstantWithdraw` (line 356) and the default/loss claim paths (`_claimDefaultedWithdrawRequest`, `_claimLossAdjustedWithdrawRequest`).

Concretely, with the CDO in buffer phase: Alice (whitelisted) calls `depositAA`, receives AA tranche tokens, then `tranche.transfer(bob, amount)` to Bob (never whitelisted). During the running epoch Bob holds the tranches and accrues the epoch yield; after `stopEpoch` (or via instant withdraw when prefunded), Bob calls `requestWithdraw` on the CDO, the CDO calls `IdleCreditVault.requestWithdraw`, and after the wait epoch Bob calls `claimWithdrawRequest` and receives underlying. At no point does any contract verify `isWalletAllowed(bob)`.

### Impact Explanation
The whitelist gate that is supposed to restrict who may hold a credit position and receive borrower repayments is fully bypassable. Any non-KYC'd address can hold tranches, earn yield, and redeem/claim underlying — including default-recovery claims via `DefaultDistributor.claim`/loss-adjusted paths. This defeats the stated compliance purpose of the Keyring gate on the credit CDOs. Financially it does not directly drain other LPs' funds (it is an access-control violation rather than an inflation bug), so severity tracks the external finding: Medium.

### Likelihood Explanation
High: requires only one whitelisted lender willing to act as entry point (or a KYC'd lender whose tokens are acquired OTC/on secondary). No privileged misbehavior needed; the attacker can be the unprivileged recipient EOA. Any epoch phase works since tranche transfers have no gating.

Note: I verified the token is a plain ERC20 and that withdraw/claim paths carry no whitelist check; the exact line ranges of the `isWalletAllowed` invocation inside `IdleCDOEpochVariant`'s deposit functions were not retrievable in the truncated search output, but the whitelist is deposit-only by design in this codebase (no credential check exists in `IdleCreditVault` or `IdleCDOTranche`).

### Recommendation
Either enforce credentials on all value-moving paths, or acknowledge transferability. To close the hole:
- Override `_transfer`/`_beforeTokenTransfer` in `IdleCDOTranche` (or gate via the CDO) to require `checkCredential` for the `to` address on non-mint/burn transfers, and/or
- Add an `isWalletAllowed(_user)` check in `IdleCDOEpochVariant.requestWithdraw`/`withdrawInstant` entry points so claims revert for non-whitelisted users.

If secondary transferability is intended, document that the whitelist only controls entry, not holding/exiting.

### Proof of Concept
Foundry fork sketch (mainnet-fork against a live `IdleCDOEpochVariant` credit pool, e.g. via `IdleCreditVaultFactory` deployment):

```solidity
// test/foundry/WhitelistBypass.t.sol
function test_TrancheTransferBypassesWhitelist() public {
    // alice is whitelisted via KeyringIdleWhitelist.setWhitelistStatus or Keyring policy
    // bob is NOT whitelisted
    uint256 amount = 1_000_000e6; // USDC
    deal(token, alice, amount);

    // buffer phase: alice deposits
    vm.startPrank(alice);
    IERC20(token).approve(address(cdo), amount);
    cdo.depositAA(amount);
    uint256 trancheBal = IERC20(address(AAtranche)).balanceOf(alice);
    // unrestricted ERC20 transfer to non-whitelisted bob
    AAtranche.transfer(bob, trancheBal);
    vm.stopPrank();

    // manager runs epoch
    vm.prank(manager); cdo.startEpoch();
    vm.prank(manager); cdo.stopEpoch(interestParams); // yield accrues to bob

    // bob (never whitelisted) requests and claims withdrawal
    vm.startPrank(bob);
    cdo.requestWithdraw(trancheBal, true /*AA*/); // -> IdleCreditVault.requestWithdraw
    vm.stopPrank();

    // advance an epoch / fund pendingWithdraws via borrower repay, then:
    vm.prank(bob);
    cdo.claimWithdrawRequest(); // -> IdleCreditVault.claimWithdrawRequest -> _transferFundedClaim(bob, ...)
    assertGt(IERC20(token).balanceOf(bob), 0); // bob exits with principal+yield despite no KYC
}
```

Assertions: `KeyringIdleWhitelist.checkCredential(policyId, bob) == false` throughout; `transfer` succeeds because `IdleCDOTranche` has no hook; `claimWithdrawRequest` pays bob because `_transferFundedClaim` only guards the recovery reserve, not the recipient's credential status.