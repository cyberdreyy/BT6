### Title
Access-token flashloan enables theft of safety deposits via publicCancel/publicWithdraw - (File: contracts/BaseEscrow.sol)

### Summary
`BaseEscrow.onlyAccessTokenHolder` gates the public `withdraw`/`cancel` paths with a single-block `IERC20.balanceOf` check. Because `publicCancel` and `publicWithdraw` complete atomically and pay the escrow’s `safetyDeposit` to `msg.sender`, an unprivileged attacker can flashloan the access token, satisfy the balance check, claim the safety deposit, and repay the loan in one transaction. This is the same root cause as the Aragon `EarlyExecution` flashloan: a token balance is used to authorize a privileged action that finishes before the token must be returned.

### Finding Description
- `BaseEscrow.sol:63-66` defines `onlyAccessTokenHolder`, which reverts only when `_ACCESS_TOKEN.balanceOf(msg.sender) == 0`.
- `EscrowSrc.sol:68-75` and `EscrowSrc.sol:97-103` apply this modifier to `publicWithdraw` and `publicCancel`.
- `EscrowDst.sol:50-57` applies it to `publicWithdraw`.
- None of these functions enforce a holding period, delegation, or non-transferable token ownership; they only inspect the caller’s balance at the moment of the call.
- `EscrowSrc._cancel` (`EscrowSrc.sol:125-132`) and `EscrowSrc._withdrawTo` (`EscrowSrc.sol:111-119`) transfer `immutables.safetyDeposit` to `msg.sender`; `EscrowDst._withdraw` (`EscrowDst.sol:79-96`) does the same.
- `publicCancel` requires no secret, only the access token and the public-cancellation timelock window.

### Impact Explanation
An attacker can steal the safety deposit locked in an escrow. For `EscrowSrc.publicCancel`, the attacker needs only to wait until `SrcPublicCancellation` begins, flashloan the access token, and call the function; the escrow’s native safety deposit is sent to the attacker. For `publicWithdraw`, the attacker additionally needs the secret, but the safety deposit is still redirected from the intended taker/resolver to the flashloan attacker. This is direct theft of user funds (the taker’s safety deposit) and falls under the Critical/High bounty scope for theft of funds in production contracts.

### Likelihood Explanation
The attack is atomic, requires no privileged role, and is publicly triggerable through `publicCancel`/`publicWithdraw`. If the configured access token is any liquid ERC20 with flashloan markets (or even a transferable token that can be borrowed), the attack is practical. `publicCancel` is the most reliable path because it needs no secret. Even if the token is currently illiquid, the contract relies on a point-in-time balance snapshot, so any future lending/flashloan market or large holder loan breaks the invariant.

### Recommendation
Do not use a flashloanable ERC20 balance for same-transaction access control. Prefer:
1. A factory-maintained registry of approved resolver accounts, replacing `onlyAccessTokenHolder`.
2. A time-weighted or delegated balance check that requires the token to be held before the block of the call.
3. A non-transferable soulbound access token with ownership tracked in a dedicated contract.
4. Reconsider paying the full `safetyDeposit` to `msg.sender` on public execution; instead refund it to the taker/maker or split it so the executor portion is not stealable through a flashloan.

### Proof of Concept
```solidity
// Attacker contract
function attack(IBaseEscrow.Immutables calldata immutables) external {
    // 1. Flashloan enough access token to make balanceOf > 0
    accessToken.flashLoan(address(this), 1, "");

    // 2. In the flashloan callback:
    escrowSrc.publicCancel(immutables);
    // safetyDeposit native tokens are now in this contract

    // 3. Repay flashloan
    accessToken.transfer(flashLoanProvider, 1 + fee);
}

// For publicWithdraw the same pattern, with the secret known:
// escrowSrc.publicWithdraw(secret, immutables);
// escrowDst.publicWithdraw(secret, immutables);
```

This mirrors the Aragon PoC: a flashloaned token temporarily grants a privileged capability (`early execute` there, `publicCancel`/`publicWithdraw` here), the action pays out value to the attacker, and the token is returned in the same transaction.