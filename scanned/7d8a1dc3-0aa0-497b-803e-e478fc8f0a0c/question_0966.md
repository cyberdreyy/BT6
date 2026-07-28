# Q966: Src withdrawTo replay after refill just_before_rescue almost_full

## Question
After a successful `EscrowSrc.withdrawTo`, can the taker reuse the same secret at just before `RESCUE_DELAY` and direct a second maker-token refill of `amount - 1` to a new attacker-controlled target because `withdrawTo()` never records that the source escrow was already spent?

## Target
- File/function: `contracts/EscrowSrc.sol::withdrawTo`
- Entrypoint: `EscrowSrc.withdrawTo(bytes32,address,IBaseEscrow.Immutables)`
- Attacker controls: the secret, the second `target` address, replay timing, and monitoring for any later maker-token refill
- Exploit idea: Replay the private withdraw-to path to redirect later inbound maker tokens to a new recipient.
- Invariant to test: Changing the `target` after the first successful withdrawal must not let the same escrow release a second maker-token payout.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Call `withdrawTo` once, transfer `amount - 1` of maker token to the clone, then call `withdrawTo` again with a different target and verify whether the second target receives funds.
