# Q813: Src withdraw replay after refill before_public_open small

## Question
After one successful `EscrowSrc.withdraw`, can the same unprivileged taker wait until one block before `SrcPublicWithdrawal` and replay `withdraw(secret, immutables)` once a later inbound maker-token transfer of `amount / 10` reaches the clone, causing the source escrow to release more than its original `immutables.amount` because no spent flag blocks a second withdrawal with the same secret?

## Target
- File/function: `contracts/EscrowSrc.sol::withdraw`
- Entrypoint: `EscrowSrc.withdraw(bytes32,IBaseEscrow.Immutables)`
- Attacker controls: the secret, call timing inside the source withdrawal window, and observation of later inbound maker-token transfers into the clone
- Exploit idea: Replay the private withdraw path after the first execution because the clone keeps no spent state.
- Invariant to test: One source escrow lifecycle should never let repeated `withdraw()` calls extract more than the single authorized maker-token amount.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Execute `withdraw`, transfer `amount / 10` of the maker token into the same clone before `SrcCancellation`, call `withdraw` again with the same secret, and check whether the second call transfers it.
