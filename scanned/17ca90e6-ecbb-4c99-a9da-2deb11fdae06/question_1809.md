# Q1809: Src withdraw reentrancy can amplify refill after_private_open double

## Question
Can a taker-controlled contract enter through `EscrowSrc.withdraw()` immediately after `SrcWithdrawal` opens, receive the native safety-deposit refund in its fallback, and reenter the same clone before the outer call returns to pull a later maker-token refill of `amount * 2` or another payable balance because no reentrancy guard or terminal-state flag exists?

## Target
- File/function: `contracts/EscrowSrc.sol::withdraw`
- Entrypoint: `EscrowSrc.withdraw(bytes32,IBaseEscrow.Immutables)` from a contract caller
- Attacker controls: a contract taker with a payable fallback, the secret, reentrancy timing, and observation of later balances
- Exploit idea: Use the native refund callback to reenter the same source clone during withdrawal settlement.
- Invariant to test: The refund transfer at the end of `withdraw()` must not let the same escrow execute a second payout path.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Call `withdraw()` from a contract with a reentering fallback, arrange a later refill of `amount * 2`, and assert that the fallback cannot extract extra funds before the outer call completes.
