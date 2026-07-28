# Q3434: PostInteraction amount rounding can underfund source or destination floor_down src_boundary

## Question
Can an unprivileged filler use floor-style rounding down with `makingAmount` on a partial-fill boundary so that `_postInteraction()` records a source `immutables.amount`, destination complement amount, or fee set that no longer matches what the fill actually transferred, creating an underfunded escrow, an overpromised payout, or a frozen settlement?

## Target
- File/function: `contracts/BaseEscrowFactory.sol::_postInteraction`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` through the maker-amount or taking-amount path
- Attacker controls: the choice of maker-vs-taking amount path, the exact fill amount, and fee-related order settings
- Exploit idea: Look for divergence between settlement-side transferred value and the immutable amounts emitted and hashed into the clone.
- Invariant to test: The amounts encoded into source and destination escrows must exactly match the value transferred during order fill.
- Expected Immunefi impact: Protocol insolvency
- Fast validation: Fill orders using floor-style rounding down and `makingAmount` on a partial-fill boundary, compare transferred balances against emitted and hashed immutable amounts, and check for underfunded escrows.
