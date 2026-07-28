# Q2932: Dst withdraw native recipient reentrancy can desync payouts before_cancel fees_small

## Question
When the destination asset is native and fees sum to a tiny dust value, can a malicious maker, protocol-fee recipient, or integrator-fee recipient receive one of the early `_uniTransfer()` calls during `EscrowDst.withdraw()` at one block before `DstCancellation` and reenter the same clone before the later transfers execute, causing duplicated payout, skipped fee accounting, or a stuck destination settlement?

## Target
- File/function: `contracts/EscrowDst.sol::withdraw`, `contracts/libraries/ImmutablesLib.sol`
- Entrypoint: `EscrowDst.withdraw(bytes32,IBaseEscrow.Immutables)` with native destination payout
- Attacker controls: the destination token mode, the fee recipients, the maker recipient, the secret, and a reentering fallback contract
- Exploit idea: Exploit native-token recipient callbacks during the ordered fee and maker transfers in `_withdraw()`.
- Invariant to test: Destination fee and maker payouts must remain atomic and non-reentrant even when native recipients can execute code.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Use native destination payout with fees sum to a tiny dust value, set a reentering recipient, call `withdraw()` at one block before `DstCancellation`, and inspect whether fallback reentry can alter later payouts or duplicate execution.
