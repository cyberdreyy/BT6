### Title
Withdraw-receipt permission leak: transferable strategy-token receipts let a non-KYC wallet redeem funds through ungated claim functions — (File: contracts/strategies/idle/IdleCreditVault.sol, contracts/IdleCDOEpochVariant.sol)

### Summary
CVE-2024-10458 is a permission leak: a security check enforced on a trusted origin is silently bypassed when a capability (embed/object permission) is exercised from an untrusted context. The analog in IdleCreditVault is identical in structure: Keyring/KYC gating (`isWalletAllowed`) is enforced only on *entry* paths (`_deposit`, `requestWithdraw`, `depositDuringEpoch`), while the *redemption* capability — the withdraw receipt minted as transferable strategy tokens — can be exercised by `claimWithdrawRequest`/`claimInstantWithdrawRequest`, which never re-check `isWalletAllowed(msg.sender)`. The credential granted to a trusted wallet leaks to any untrusted wallet holding the receipt token.

### Finding Description
`IdleCDOEpochVariant.requestWithdraw` gates callers with `!isWalletAllowed(msg.sender)` [1](#0-0) , and `_deposit`/`depositDuringEpoch` do the same [2](#0-1) . However, the actual payout paths — `claimWithdrawRequest()` and `claimInstantWithdrawRequest()` — contain no wallet check at all [3](#0-2) .

In `IdleCreditVault`, both `requestWithdraw` and `requestInstantWithdraw` mint strategy tokens to `_user` as a fungible withdrawal receipt [4](#0-3) [5](#0-4) . The claim functions burn `balanceOf(_user)` receipts and pay underlyings to `_user` via `_transferFundedClaim` [6](#0-5) [7](#0-6) . Nothing in the receipt token restricts transfers to allowed wallets (the token is a plain ERC20Upgradeable; the only guards are `_onlyIdleCDO` on vault entry points).

So the KYC'd depositor's redemption permission is embodied in a bearer token. Once minted, the trusted-site permission "embeds" into whatever untrusted wallet holds it.

### Impact Explanation
An unprivileged attacker sequence:

1. KYC'd wallet `K` deposits, then calls `cdoEpoch.requestWithdraw(...)` (or instant path) — allowed because `isWalletAllowed(K)` is true. The vault mints `K` strategy-token receipts representing `N` underlyings.
2. `K` transfers the receipts to attacker `A`, a wallet that fails `isWalletAllowed` (no Keyring credential, sanctioned, or a contract that could never pass KYC).
3. After funding (`collectInstantWithdrawFunds` / next epoch), `A` calls `cdoEpoch.claimInstantWithdrawRequest()` (or `claimWithdrawRequest()`). The vault burns `A`'s receipts and transfers funded underlyings directly to `A` — no credential check anywhere on this path.

The pool's compliance boundary ("only credentialed wallets may take underlying out") is broken: the effective withdrawal permission leaked from a trusted to an untrusted principal. Quantified impact equals the full funded value of the receipts transferred; for instant withdrawals this is immediate liquidity drained by a wallet that could never legally hold a position. The same leak applies post-default: `_claimPostDefaultWithdrawRequest`/`_claimDefaultedWithdrawRequest` also pay `balanceOf(_user)` receipts with no credential check, so recovery funds can leak to non-KYC holders.

### Likelihood Explanation
Likelihood is gated by one fact I could not fully verify within the indexed code: whether `IdleCreditVault` restricts `_transfer` of strategy tokens. If receipts are freely transferable (the code shows ordinary `_mint`/`_burn` ERC20 usage with no `_beforeTokenTransfer` hook surfaced), the exploit is a two-transaction, unprivileged attack requiring only a cooperating KYC'd seller — a normal secondary-market transaction. Even if transfers were restricted, the leak still exists for *credential revocation*: a wallet that passed KYC at request time but is later revoked/sanctioned retains claim rights, since no claim path re-evaluates `isWalletAllowed`. Guards that do exist (`_onlyIdleCDO`, `allowInstantWithdraw`, epoch gating in `_claimFundedWithdrawRequest`) control *when* funds are claimable, not *who* may claim.

### Recommendation
Either (a) add `isWalletAllowed(msg.sender)` checks to `claimWithdrawRequest` and `claimInstantWithdrawRequest` in `IdleCDOEpochVariant` (mirroring `requestWithdraw`), or (b) if claims are intentionally permissionless, override `_transfer`/`transfer`/`transferFrom` on the strategy receipt token to enforce `isWalletAllowed(to)` so the bearer capability cannot move to an uncredentialed holder. The WriteOffEscrow already demonstrates the intended design tension: it deliberately lets anyone buy tranche tokens but relies on downstream KYC gates — those gates are currently absent on the claim path, which is the final underlyings payout.

### Proof of Concept
```solidity
// Fork test sketch (Foundry), assumes strategyToken receipt is unrestricted ERC20
function testReceiptPermissionLeak() public {
    // Setup: keyring enabled, attacker fails credential
    vm.prank(owner);
    cdoEpoch.setKeyringParams(keyring, POLICY_ID);
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector, attacker), abi.encode(false));
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector, kycUser), abi.encode(true));

    // 1. KYC user deposits and requests instant withdraw during running epoch
    vm.startPrank(kycUser);
    idleCDO.depositAA(DEPOSIT);
    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.stopPrank();

    // receipts minted to kycUser
    uint256 receipt = IdleCreditVault(strategy).balanceOf(kycUser);
    assertEq(receipt, requested);

    // 2. Transfer bearer receipt to non-KYC attacker
    vm.prank(kycUser);
    IERC20(strategy).transfer(attacker, receipt);

    // 3. Fund the instant withdraw (honest manager path)
    _startEpoch(); // startEpoch moves funds; manager collects instant funds
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();

    // 4. Attacker — never KYC'd — redeems underlying directly
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest(); // no isWalletAllowed check -> succeeds
    assertEq(underlying.balanceOf(attacker) - balPre, requested);
}
```

Caveat: if a full-repo review reveals an unindexed `_transfer` guard on the strategy token, step 2 reverts and the residual issue narrows to post-revocation claims (step 4 executed by the originally-KYC'd, now-revoked user), which is still a permission leak but with reduced severity.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L644-650)
```text
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L741-744)
```text
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L967-979)
```text
  function claimWithdrawRequest() external {
    // underlyings requested, here we check that user waited at least one epoch and that borrower
    // did not default upon repayment (old requests can still be claimed)
    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
  }

  /// @notice Claim an instant withdraw request from the vault. Can be done when epoch is running
  /// as funds will get transferred from borrower when epoch starts
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L274-275)
```text
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L341-349)
```text
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L362-366)
```text
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```
