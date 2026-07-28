# Q2305: Dst withdraw can freeze on short parameters len_0 fees_exact

## Question
Can an unprivileged destination-escrow creator call `createDstEscrow()` with an empty `parameters` blob while fees sum exactly to `immutables.amount`, so that `EscrowDst.withdraw()` later reverts inside `ImmutablesLib` when it tries to read fee fields, permanently or temporarily blocking the maker payout even though the clone was successfully funded?

## Target
- File/function: `contracts/EscrowDst.sol::withdraw`, `contracts/libraries/ImmutablesLib.sol`
- Entrypoint: `BaseEscrowFactory.createDstEscrow(IBaseEscrow.Immutables,uint256)` -> `EscrowDst.withdraw(...)`
- Attacker controls: the entire `dstImmutables.parameters` blob, fee values encoded in it, and the decision to fund the destination clone
- Exploit idea: Deploy a destination clone whose fee blob is too short for later withdrawal decoding.
- Invariant to test: Every funded destination escrow should remain withdrawable with the exact parameters used to create it.
- Expected Immunefi impact: Temporary freezing of funds
- Fast validation: Create a destination escrow with an empty `parameters` blob, open the withdrawal window, call `withdraw()`, and check whether `ImmutablesLib` reverts before funds can be paid out.
