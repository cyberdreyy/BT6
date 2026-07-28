# Q1216: Src withdrawTo replay after native top-up before_public_open almost_full

## Question
After a successful `EscrowSrc.withdrawTo`, can the taker call `withdrawTo()` again at one block before `SrcPublicWithdrawal` after a later native-token top-up of `amount - 1`, collecting another `safetyDeposit` refund while also changing the visible payout target on the replayed lifecycle?

## Target
- File/function: `contracts/EscrowSrc.sol::withdrawTo`
- Entrypoint: `EscrowSrc.withdrawTo(bytes32,address,IBaseEscrow.Immutables)`
- Attacker controls: the secret, a second attacker-controlled `target`, replay timing, and any later native-token balance added to the clone
- Exploit idea: Replay `withdrawTo()` for a second refund even when no new source-token withdrawal should be available.
- Invariant to test: The `withdrawTo()` path must not let the same clone emit or pay a second live withdrawal refund.
- Expected Immunefi impact: Theft of coins or tokens intended for transaction fees
- Fast validation: Execute `withdrawTo()` once, send `amount - 1` of native token to the clone, then replay `withdrawTo()` with a different target and verify whether the second refund is paid.
