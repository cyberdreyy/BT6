# Q2300: Src boundary race can split withdraw and cancel plus_1800 zksync_src

## Question
At +1800 seconds relative to the source withdrawal/cancellation boundary for the zkSync source deployment path, can an unprivileged taker or public caller sequence `withdraw`, `publicWithdraw`, `cancel`, or `publicCancel` so that the same source escrow exposes both payout paths across an off-by-one time transition, causing duplicated execution or a one-sided freeze?

## Target
- File/function: `contracts/EscrowSrc.sol::{withdraw,publicWithdraw,cancel,publicCancel}`
- Entrypoint: direct calls to `EscrowSrc` public lifecycle functions around a timelock boundary
- Attacker controls: precise block timestamps around the source timelock edges, the secret when needed, and the choice of private or public lifecycle function
- Exploit idea: Stress the asymmetric `onlyAfter` and `onlyBefore` comparisons at the phase edges.
- Invariant to test: A single source escrow must expose exactly one valid lifecycle path at any instant around the boundary.
- Expected Immunefi impact: Temporary freezing of funds
- Fast validation: Warp to +1800 seconds around the source boundary for the zkSync source deployment path, try both withdrawal and cancellation paths in adjacent orders, and check whether any overlap or unintended gap appears.
