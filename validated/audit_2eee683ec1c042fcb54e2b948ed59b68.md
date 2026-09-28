### Title
USDC-blocklisted lender's withdrawal receipt is permanently unclaimable due to push-only payout and address-bound receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When a lender requests a withdrawal, `IdleCreditVault` mints an address-bound receipt and later pays it out with a push `safeTransfer` to the original `msg.sender` (`claimWithdrawRequest`/`claimInstantWithdrawRequest`, invoked via `IdleCDOEpochVariant.claimWithdrawRequest`/`claimInstantWithdrawRequest` at `IdleCDOEpochVariant.sol:967-979`). If the lender's address is added to the underlying token's contract-level blocklist (e.g. USDC/USDT `blacklist`) after the request is created, the payout transfer reverts forever. Because receipt claims cannot be transferred or redirected to another address (`IdleCreditVault._transfer` reverts for anyone except `idleCDO` and `setCanTransfer(true)` permanently reverts), the matured claim — principal plus interest — is permanently frozen in the vault.

### Finding Description
The flow is:

1. Lender calls `requestWithdraw` (`IdleCDOEpochVariant.sol:739`), tranche tokens are burned and a fixed claim is recorded in `IdleCreditVault` under `withdrawsRequests[msg.sender]` / instant-withdraw mapping.
2. After `stopEpoch` funds the strategy via `collectWithdrawFunds` (`IdleCDOEpochVariant.sol:408-410`), the lender calls `claimWithdrawRequest`, which delegates to `IdleCreditVault(strategy).claimWithdrawRequest(msg.sender)` (`IdleCDOEpochVariant.sol:970`) and the strategy `safeTransfer`s the underlying directly to that sender.
3. If that sender is now on the token blocklist, the transfer reverts. The receipt stays claimable-but-unpayable: `IdleCreditVault._transfer` (`IdleCreditVault.sol:939-942`) only lets the IdleCDO move receipt tokens, and `setCanTransfer` (`IdleCreditVault.sol:948-951`) can only ever set `false`, so the claim cannot be sold or moved to a clean address. There is no `claim(to)` escape hatch — unlike `DefaultDistributor.claim(address _to)` (`DefaultDistributor.sol:35-41`), which correctly lets the claimant specify a recipient.

The same push pattern exists in `IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest` (`IdleCreditVaultWriteOffEscrow.sol:149`), where a blocklisted lender's fulfillment reverts — but there the lender can self-rescue via `deleteWriteOffRequest` (`:105-116`), since the returned asset is the tranche token, not the blocklisted underlying. For withdrawal receipts no such rescue exists: the only payout asset is the blocklisted underlying and the only payout path is `msg.sender`.

This is the direct analog of the Teller issue: repayment/claim settlement depends on a push transfer to a fixed counterparty, with no pull-over-push fallback. Unlike the borrower-side flows here, which deliberately wrap pushes in try/catch (`sendFundsToBorrower` at `IdleCDOEpochVariant.sol:295-303`, `getFundsFromBorrower` at `:408-505`), the lender-claim path has no fallback at all.

### Impact Explanation
Permanent freezing of funds. A KYC-passing lender who is blocklisted by the token issuer (USDC, USDT, PAXG, etc.) between `requestWithdraw` and claim permanently loses the entire matured claim (principal + epoch interest, arbitrarily large — e.g. the full 10,000+ USDC claims exercised in `test/foundry/IdleCreditVault.t.sol:4684-4687`). The vault keeps the underlying and the receipt mapping but can never pay it out, and no other account can claim or receive it on the user's behalf.

### Likelihood Explanation
Requires an external, non-protocol event (token-issuer blocklisting), so likelihood is low-to-medium — identical in kind to the referenced Teller High finding. Blocklisting does happen (sanctions, exchange freezes) and credit vaults explicitly target regulated, KYC'd lenders holding large positions, which are exactly the counterparties most exposed to compliance actions. No unprivileged attacker can force the blocklist, but no privileged actor can undo the freeze either: owner rescue methods only move tokens out of contracts, not reassign a user's claim.

### Recommendation
Adopt pull-over-push with a recipient parameter for claims: add `claimWithdrawRequest(address _to)` / `claimInstantWithdrawRequest(address _to)` in `IdleCreditVault` (and pass-through in `IdleCDOEpochVariant`) so a blocked claimant can route payout to a fresh address, mirroring `DefaultDistributor.claim(address _to)`. Alternatively, let a blocked claimant delegate the claim to a `msg.sender`-approved receiver.

### Proof of Concept
Foundry fork test (mainnet USDC). Outline:

```solidity
function testBlocklistedLenderCannotClaimReceipt() external {
    uint256 amountWei = 10_000 * ONE_SCALE;
    address user = makeAddr("user");

    // buffer phase: user deposits and requests a withdraw
    _depositWithUser(user, amountWei, true);
    vm.prank(user);
    uint256 claimAmount = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // run epoch 0 so the receipt matures and funds reach the strategy
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // token issuer (USDC blacklister) adds the user to the blocklist AFTER the request
    address blacklister = FiatTokenV2(USDC).blacklister();
    vm.prank(blacklister);
    FiatTokenV2(USDC).blacklist(user);

    // claim reverts forever: push transfer to a blocklisted recipient
    vm.prank(user);
    vm.expectRevert(); // USDC: recipient is blacklisted
    cdoEpoch.claimWithdrawRequest();

    // no rerouting possible: receipts are address-bound
    vm.prank(user);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    IERC20Detailed(address(strategy)).transfer(makeAddr("clean"), claimAmount);

    // funds are permanently stuck: claim still recorded, underlying still in vault
    assertEq(IdleCreditVault(address(strategy)).withdrawsRequests(user), claimAmount);
}
```

Expected result: `claimWithdrawRequest` always reverts post-blocklist, the receipt cannot be transferred, and `claimAmount` underlying remains locked in `IdleCreditVault` indefinitely — permanent loss of the matured claim for that lender.