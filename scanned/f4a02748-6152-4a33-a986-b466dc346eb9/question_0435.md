# Q435: Same-block fill reordering can desynchronize band 34

## Question
If two unprivileged fillers race the same multiple-fill order in one block, can one filler use a proof for band `34` while another uses an adjacent band so that `lastValidated[key]` is overwritten in a stale order and `_postInteraction()` evaluates `remainingMakingAmount` against the wrong validated index, causing duplicated execution or a frozen escrow lifecycle?

## Target
- File/function: `contracts/MerkleStorageInvalidator.sol::takerInteraction`, `contracts/BaseEscrowFactory.sol::_postInteraction`, `contracts/BaseEscrowFactory.sol::_isValidPartialFill`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> taker interaction -> postInteraction
- Attacker controls: transaction ordering, fill amount for band `34`, competing proof/index pairs, and same-block race timing
- Exploit idea: Exploit ordering between `takerInteraction()` state writes and `_postInteraction()` checks.
- Invariant to test: Concurrent fills for one order should never let `lastValidated[key]` move backward or be consumed against the wrong cumulative amount.
- Expected Immunefi impact: Temporary freezing of funds
- Fast validation: Broadcast two fills for adjacent bands around `34` in the same block on a local fork and inspect whether one ordering deploys an escrow that should have been rejected.
