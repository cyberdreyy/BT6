# Q3667: Custom postInteraction can skew deployment integrator_share multiple_fill_short

## Question
Can a maker-authored order that uses a non-zero integrator revenue share and the multiple-fill root blob is present but shorter than expected include a custom tail `IPostInteraction` target that reenters settlement or mutates assumptions before the source clone is checked for balances, causing `_postInteraction()` to deploy or validate the wrong escrow state?

## Target
- File/function: `contracts/BaseEscrowFactory.sol::_postInteraction`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` with a custom post-interaction tail
- Attacker controls: the custom post-interaction target and calldata, the fee blob, and the chosen settlement path
- Exploit idea: Use the externally called tail before final source-balance checks to disturb the assumed state.
- Invariant to test: External custom post-interaction logic must not let the order fill complete with a different escrow state than the factory expects.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Attach a reentering custom post-interaction target to an order using a non-zero integrator revenue share and the multiple-fill root blob is present but shorter than expected, then inspect whether clone deployment or balance checks can be bypassed or desynchronized.
