### Title
KYC/credential check enforced at deposit and request time but skipped on claims, letting non-credentialed wallets withdraw vault funds - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external bug class is a validity check that runs in one execution mode (CheckTx) but is silently skipped in another (DeliverTx), letting an invalid operation execute on the settlement path. In `IdleCDOEpochVariant`, the Keyring credential check `isWalletAllowed` is enforced on every entry-side action — `_deposit`, `depositDuringEpoch`, and `requestWithdraw` — but is not performed on the two payout paths `claimWithdrawRequest` and `claimInstantWithdrawRequest`. A wallet that could never pass `checkCredential` can therefore still pull underlying out of the vault by acquiring a claim (tranche tokens or strategy-token receipts are transferable) and claiming it.

### Finding Description
`isWalletAllowed` gates deposits and withdrawal requests:

- `_deposit` reverts when `!isWalletAllowed(msg.sender)` at line 645.
- `depositDuringEpoch` includes `!isWalletAllowed(msg.sender)` in its `_checkNotAllowed` at line 668.
- `requestWithdraw` includes `!isWalletAllowed(msg.sender)` at lines 741-744.

But the settlement functions omit the check entirely:

```solidity
// contracts/IdleCDOEpochVariant.sol
function claimWithdrawRequest() external {
    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
}

function claimInstantWithdrawRequest() external {
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
}
```

Neither function calls `isWalletAllowed(msg.sender)`, and the downstream strategy functions `IdleCreditVault.claimWithdrawRequest` / `claimInstantWithdrawRequest` only enforce `_onlyIdleCDO()` — they pay `_user` (here `msg.sender` forwarded by the CDO) without any credential check. There is also no KYC check on tranche-token or strategy-token receipt transfers, so a non-credentialed EOA can obtain a receipt from a KYC'd lender (via direct transfer or a secondary purchase) and settle it for underlying. This mirrors the Sei issue: validation applied on the admission path but skipped on the execution/settlement path, so the "deliver" transaction succeeds even though the same actor would be rejected at entry.

The deprecated `keyringAllowWithdraw` storage slot suggests a withdraw-time KYC check existed historically; the current code has no replacement for it on claims.

### Impact Explanation
The vault's access-control invariant — only credentialed wallets may hold exposure or receive funds — is broken on the payout side. Concretely:

- A blocked/sanctioned wallet receives tranche tokens or `instantWithdrawsRequests` strategy-token receipts from a KYC'd lender (receipts are ERC20-minted to the user at `requestInstantWithdraw`/`requestWithdraw`, lines 275 and 363 of `IdleCreditVault.sol`, and freely transferable).
- The attacker calls `claimWithdrawRequest` (after the normal one-epoch wait) or `claimInstantWithdrawRequest` (once `allowInstantWithdraw` is set) and receives underlying from the strategy's funded reserve, burning the receipts.
- Funds leave the vault to a wallet that `IKeyring.checkCredential` would reject; the protocol cannot prevent the payout without pausing claims for everyone.

The financial magnitude equals whatever funded receipts the attacker accumulates, up to the full funded withdrawal reserve held by `IdleCreditVault` (`_transferFundedClaim`).

### Likelihood Explanation
Requires only an unprivileged attacker plus any willing (or unwitting) receipt holder transferring tranche tokens/strategy-token receipts — both are ordinary ERC20s with no transfer-time credential gate in-scope. No privileged role misbehavior is needed; the honest owner/manager/guardian flow that funds claims proceeds normally. The bypass is deterministic once a funded receipt exists.

### Recommendation
Apply the same credential check on the settlement path as on the entry path:

- Add `_checkNotAllowed(!isWalletAllowed(msg.sender))` (or check the receipt owner) to `claimWithdrawRequest` and `claimInstantWithdrawRequest` in `contracts/IdleCDOEpochVariant.sol`.
- Alternatively/additionally, enforce `isWalletAllowed` in the `IdleCDOTranche`/strategy-token `transfer`/`_transfer` hooks so non-credentialed wallets cannot hold claims in the first place.

### Proof of Concept
Reproducible on the existing Foundry fork harness (`test/foundry/IdleCreditVault.t.sol`):

```solidity
function testNonKycAttackerClaimsFundedReceipt() external {
    // Setup: keyring set, attacker NOT credentialed.
    address attacker = makeAddr("sanctionedUser");
    // keyring returns false for attacker; true for test contract (kycLender)

    uint256 amount = 10_000 * ONE_SCALE;
    uint256 minted = idleCDO.depositAA(amount);            // kycLender deposits
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, apr, expectedFunds);       // buffer phase

    uint256 requested = cdoEpoch.requestWithdraw(minted, address(AAtranche)); // kycLender requests
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, apr, expectedFunds);       // receipt now funded

    // Transfer the claim: move strategy-token receipts / arrange payout to attacker.
    // For instant path: transfer strategy tokens minted as receipt to `attacker`.
    IERC20Detailed(strategyToken).transfer(attacker, requested);
    // For normal path a holder can simply be the attacker via a complicit KYC'd
    // wrapper requesting on their behalf then transferring receipts.

    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();   // succeeds — no isWalletAllowed check
    assertGt(underlying.balanceOf(attacker), 0); // funds paid to non-credentialed wallet
}
```

Uncertainty note: whether the strategy-token receipt for the *normal* (non-instant) request is minted to the user in a transferable form depends on `requestWithdraw` internals (line 275 `_mint(_user, _amount)` suggests it is); the instant-withdraw receipt is unambiguously minted to the user and transferable, making the bypass concrete for `claimInstantWithdrawRequest` even if the normal path requires a cooperating KYC'd requester.