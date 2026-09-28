### Title
KYC revocation only blocks new actions - revoked wallets can still transfer tranche tokens and claim withdrawals, so the freeze is one-directional - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
`isWalletAllowed()` is enforced only on entry points that initiate a position (`_deposit`, `depositDuringEpoch`, `requestWithdraw`, and the queue's `requestDeposit`/`requestWithdraw`). It is not enforced on (a) tranche-token transfers, (b) `claimWithdrawRequest`, (c) `claimInstantWithdrawRequest`, or (d) `WriteOffEscrow.fullfillWriteOffRequest`, which delivers tranche tokens to any buyer. Like the reference bug (`_beforeTokenTransfer` only checking the destination), the credential check freezes funds in only one direction: a wallet whose Keyring credential is revoked, or that was never credentialed, can still move its tranche tokens to a credentialed address and fully exit, or directly claim any pending withdrawal itself. The suite even encodes this gap as expected behavior in `testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw`, which only asserts that a non-KYC buyer cannot call `requestWithdraw`, while he still receives the tranche tokens.

### Finding Description
The check is applied per-call on `msg.sender`:

- `IdleCDOEpochVariant._deposit`: `_checkNotAllowed(!isWalletAllowed(msg.sender))` (line 645).
- `IdleCDOEpochVariant.depositDuringEpoch`: `!isWalletAllowed(msg.sender)` in the revert condition (line 668).
- `IdleCDOEpochVariant.requestWithdraw`: `!isWalletAllowed(msg.sender)` (line 743).
- `IdleCDOEpochQueue.requestDeposit`/`requestWithdraw`: `_checkAllowed(msg.sender)` → `cdoEpoch.isWalletAllowed(wallet)` (lines 105, 133, 415-421).

But every exit path skips it:

1. `IdleCDOTranche` is a plain `ERC20` with no `_beforeTokenTransfer` hook, no `minter`-restricted transfer, and no KYC hook — `transfer`/`transferFrom` are unrestricted (`contracts/IdleCDOTranche.sol:7-41`). A revoked holder can send tranche tokens to any address.
2. `claimWithdrawRequest` and `claimInstantWithdrawRequest` (lines 967-979) contain no `isWalletAllowed` check; they forward `msg.sender` to `IdleCreditVault.claimWithdrawRequest(_user)` / `claimInstantWithdrawRequest(_user)` (IdleCreditVault.sol:301-314, 380-393), which burn the user's receipt strategy tokens and `_transferFundedClaim(_user, amount)` with no credential check.
3. `IdleCreditVault` receipt strategy tokens minted to `_user` in `requestWithdraw`/`requestInstantWithdraw` (lines 275, 363) are also unrestricted ERC20.
4. `WriteOffEscrow.fullfillWriteOffRequest` pays underlyings and delivers tranche tokens to any `buyer` without calling `isWalletAllowed` (confirmed by `testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw`, `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol:247-294`).

So a wallet that passed KYC, deposited, and later had its credential revoked (or that acquired tranche tokens on the secondary market via the escrow or a plain transfer) can still realize the full value of its position: either claim pending withdrawals directly, or transfer tranche tokens to a credentialed accomplice who calls `requestWithdraw` + `claimWithdrawRequest` and returns the proceeds. The freeze enforced by `isWalletAllowed` is bypassable in the outbound direction, exactly matching the reference finding where the blacklisted owner could move funds out because only `destination` was checked.

### Impact Explanation
Broken invariant: access control. The Keyring-gated perimeter intends that only credentialed wallets hold exposure to the credit vault. In practice revocation (or never holding a credential) does not prevent a wallet from extracting principal plus accrued yield. The exfiltratable amount equals the holder's entire tranche position value (`_trancheToUnderlyings(balance)` plus pending withdraw-request receipts), i.e., the full quantified position can leave through the ungated paths. This defeats the compliance freeze: a sanctioned or de-credentialed lender can launder the position through a credentialed address within the same buffer period, before `stopEpoch` even runs.

### Likelihood Explanation
Likelihood is conditional on an enforcement event (Keyring revocation or a non-KYC secondary acquisition), which is an expected operational flow — `KeyringIdleWhitelist.setWhitelistStatus` exists precisely to toggle entities off (contracts/KeyringIdleWhitelist.sol:102-112), and the tranche tokens trade freely. Once triggered, the bypass is deterministic: no privileged action, timing race, or protocol state is required; the revoked holder just calls `transfer` on `IdleCDOTranche` and the accomplice redeems through the standard `requestWithdraw`/`claimWithdrawRequest` flow, or the revoked holder claims an already-matured request directly since claim paths have no gate.

### Recommendation
Enforce the credential check on exits and on token movement, mirroring the reference fix:

```diff
 function claimWithdrawRequest() external {
+    _checkNotAllowed(!isWalletAllowed(msg.sender));
     IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
 }

 function claimInstantWithdrawRequest() external {
     _checkNotAllowed(!allowInstantWithdraw);
+    _checkNotAllowed(!isWalletAllowed(msg.sender));
     IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
 }
```

And in `IdleCDOTranche`, override `_beforeTokenTransfer` to consult `IIdleCDOEpochVariant(minter).isWalletAllowed(to)` (allowing mint/burn by `minter` and optionally exempting the escrow), and add an `isWalletAllowed` check for the buyer inside `WriteOffEscrow.fullfillWriteOffRequest`. Note this also blocks claims for wallets that lose credentials mid-queue — decide whether claims should pay to a credentialed recovery address instead of reverting.

### Proof of Concept
Foundry sketch on a mainnet fork (vault deployed via `IdleCreditVaultFactory`, Keyring mocked or a real policy):

```solidity
function testRevokedLenderExits() external {
    address revoked = makeAddr("revokedLP");
    address mule    = makeAddr("kycMule");
    address keyring = address(1);

    vm.prank(owner);
    cdoEpoch.setKeyringParams(keyring, 1);

    // 1) `revoked` passes KYC and deposits during buffer
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector, 1, revoked), abi.encode(true));
    deal(address(underlying), revoked, 100_000e6);
    vm.startPrank(revoked);
    underlying.approve(address(idleCDO), 100_000e6);
    uint256 tranches = idleCDO.depositAA(100_000e6);
    vm.stopPrank();

    _startEpochAndCheckPrices(0);

    // 2) Keyring revokes `revoked` mid-epoch
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector, 1, revoked), abi.encode(false));
    assertFalse(cdoEpoch.isWalletAllowed(revoked));

    // 3) Direct exits are blocked — the "inbound-only" freeze
    vm.prank(revoked);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.requestWithdraw(tranches, address(AAtranche));

    // 4) BUG: tranche tokens transfer freely to a credentialed mule
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector, 1, mule), abi.encode(true));
    vm.prank(revoked);
    IERC20(address(AAtranche)).transfer(mule, tranches); // succeeds, no hook

    // 5) Mule requests + claims the full position; `revoked` recovered everything
    vm.prank(mule);
    cdoEpoch.requestWithdraw(tranches, address(AAtranche));
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    uint256 balPre = underlying.balanceOf(mule);
    vm.prank(mule);
    cdoEpoch.claimWithdrawRequest();
    assertGt(underlying.balanceOf(mule), balPre);
}

function testRevokedHolderStillClaims() external {
    // pre-arrange a mature withdraw request for `user`, then revoke credential;
    // claimWithdrawRequest has no isWalletAllowed check so it still pays out
    vm.mockCall(keyring, abi.encodeWithSelector(IKeyring.checkCredential.selector), abi.encode(false));
    vm.prank(user);
    cdoEpoch.claimWithdrawRequest(); // succeeds: freeze does not stop outbound claim
}
```

Both paths are unprivileged: the attacker is only a KYC-revoked (or never-credentialed) tranche holder plus an optional second credentialed EOA; no owner/manager/borrower misbehavior is involved.

Note: the finding is a broken access-control invariant rather than theft of other users' funds — if the strict acceptance bar requires third-party fund loss, this does not satisfy it, since a revoked holder only ever extracts its own position value.