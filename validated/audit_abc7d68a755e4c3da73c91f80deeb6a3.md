### Title
Instant-withdraw receipts are frozen until the borrower funds 100% of `pendingInstantWithdraws`, even though part of the request is already funded - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
The external bug requires the pool to hold the full `proposalBid` even after earlier milestones were already paid. The same class exists in the idle-tranches credit vault: a user's instant-withdraw receipt cannot be claimed at all until the borrower has funded the entire pending amount, even when a portion was already prefunded/collected by the strategy. Partial funding is locked instead of being paid out pro rata.

### Finding Description
When a user calls `requestInstantWithdraw`, the vault mints them a receipt and increases `instantWithdrawsRequests[_user]` and the global `pendingInstantWithdraws` in `contracts/strategies/idle/IdleCreditVault.sol:356-375`. Funds are collected lazily via `collectInstantWithdrawFunds`, which only decreases `pendingInstantWithdraws` by the collected amount (`IdleCreditVault.sol:398-403`), so a partial collection leaves the remainder pending.

`claimInstantWithdrawRequest` pays the user's entire `instantWithdrawsRequests[_user]` in one shot and burns the full receipt (`IdleCreditVault.sol:380-393`). On the CDO side, `IdleCDOEpochVariant.claimInstantWithdrawRequest` is gated by the `allowInstantWithdraw` flag, which is only enabled once `getInstantWithdrawFunds` has pulled the full pending amount from the borrower. If the borrower funds only part of `pendingInstantWithdraws`, the flag stays false and the claim reverts `NotAllowed` — even though the funded portion is already sitting in the contracts.

This is directly exercised by `testFinalizeDefaultClaimsFundedAndDefaultedInstantRedeemsInOneCall` in `test/foundry/IdleCreditVault.t.sol:4516-4528`: with a mixed funded/unfunded instant request, `allowInstantWithdraw()` is asserted to be false and `claimInstantWithdrawRequest` reverts `NotAllowed`. The funded share `claimData[1] - pendingInstant` cannot be claimed, exactly like the RFPSimpleStrategy recipient who cannot receive the next milestone until the pool again holds the full original bid.

### Impact Explanation
Temporary freezing of lender funds. A KYC-passed lender who requested an instant withdraw sees their entire receipt frozen until the borrower (or manager via funding) supplies the remaining pending amount — even though the protocol already holds a portion earmarked for them. In a default scenario this resolves into the recovery reserve, but in a running/stopped epoch with a slow or partially liquid borrower, the funded portion is idle and unclaimable, with the frozen amount equal to the prefunded share of the user's receipt.

### Likelihood Explanation
Requires no attacker action beyond a normal `requestInstantWithdraw` call and an ordinary partial-funding sequence around honest manager/borrower calls (`getInstantWithdrawFunds` collecting less than `pendingInstantWithdraws`, e.g. due to limited on-hand liquidity). No privileged misbehavior is needed.

### Recommendation
Allow pro-rata claims of the funded portion: track a per-epoch funded ratio (similar to `lossRecoveryPriceByEpoch` used for loss-adjusted claims in `IdleCreditVault.sol:789-801`) or enable `allowInstantWithdraw` once any amount is collected and pay `min(request, fundedShare)`, so already-collected funds are not held hostage to the unfunded remainder. Alternatively, burn only the paid fraction of the receipt instead of the full `instantWithdrawsRequests[_user]` in `claimInstantWithdrawRequest`.

### Proof of Concept
The behavior is already demonstrated by the in-repo Foundry test `testFinalizeDefaultClaimsFundedAndDefaultedInstantRedeemsInOneCall` at `test/foundry/IdleCreditVault.t.sol:4485-4551`: a user requests an instant withdraw, `getInstantWithdrawFunds` collects only part of `pendingInstantWithdraws`, `allowInstantWithdraw()` remains false, and `claimInstantWithdrawRequest` reverts `NotAllowed` until the manager completes `finalizeDefault` funding — confirming the funded portion is frozen rather than claimable.

A standalone PoC: deposit as a KYC-passed user, call `requestInstantWithdraw`, let the epoch stop with only partial instant-withdraw liquidity collected (`collectInstantWithdrawFunds` with `_amount < pendingInstantWithdraws`), then `claimInstantWithdrawRequest` reverts despite the vault/queue holding part of the user's funds.