### Title
Stale per-epoch instant-withdraw basis is never cleared on funded claims, allowing a double payout from the default recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`. These stale entries act like polluted prototype state: they are invisible during normal operation but resurrect during default finalization, letting an attacker claim default-recovery funds for an instant withdraw that was already paid at par.

### Finding Description
`requestInstantWithdraw` records the receipt basis both per-user and per-epoch (`contracts/strategies/idle/IdleCreditVault.sol:366-374`). The normal claim path, however, only zeroes `instantWithdrawsRequests[_user]` (`IdleCreditVault.sol:387-392`) — the per-epoch keys remain set forever.

When the borrower defaults and `finalizeDefaultRecovery` runs with `pendingInstantWithdraws != 0`, it sets `defaultInstantWithdrawsFinalized = true` and `defaultRecoveryEpoch = epochNumber` (`IdleCreditVault.sol:693-696`). Any later `claimInstantWithdrawRequest` first calls `_claimDefaultedInstantWithdrawRequest` (`IdleCreditVault.sol:382-385`), which reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` and pays `claimBasis * defaultRecoveryPrice` from the reserve (`IdleCreditVault.sol:842-855`).

Attack sequence (running epoch `E`, any non-APR0 mode):
1. Attacker (KYC'd lender) requests an instant withdraw of `B` in epoch `E`.
2. The vault funds it mid-epoch; attacker calls `claimInstantWithdrawRequest` and is paid `B` at par. `instantWithdrawsRequestsByEpoch[attacker][E] = B` remains stale.
3. Borrower defaults in the same epoch `E` while another user's instant request is still unfunded (`pendingInstantWithdraws != 0`), so `defaultRecoveryEpoch == E` and `defaultInstantWithdrawsFinalized == true`.
4. Attacker makes a new instant request of `≥ B` (allowed post-default; `requestInstantWithdraw` has no default guard), restoring `instantWithdrawsRequests[attacker]` and minting fresh receipt tokens so the `_burn` and the `-= claimBasis` do not underflow.
5. `claimInstantWithdrawRequest` pays the attacker `B * defaultRecoveryPrice` from `defaultRecoveryReserve` for the already-paid receipt, plus the new request at par.

### Impact Explanation
Direct theft of `defaultRecoveryReserve`: the attacker extracts up to `B * defaultRecoveryPrice` of underlying that belongs to legitimate defaulted-epoch claimants, causing reserve insolvency for later claimants. One receipt yields two payouts — the "one receipt one payout" invariant is broken. The loss is bounded by the attacker's instant-withdraw size but scales linearly and drains honest users' recovery.

### Likelihood Explanation
Requires only unprivileged actions: an instant withdraw that gets funded in the same epoch in which the borrower later defaults, plus one other unfunded instant request at finalization. Both conditions are routine — instant withdrawals are designed to be serviced mid-epoch, and defaults are the scenario this recovery code exists for. No privileged role misbehavior is needed.

### Recommendation
In `claimInstantWithdrawRequest`, clear `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (and decrement `instantWithdrawClaimsByEpoch[epochNumber]` correspondingly) when paying a funded instant claim, or track funded claims per epoch so stale basis cannot be replayed through `_claimDefaultedInstantWithdrawRequest`. Also add a default-finalized guard to `requestInstantWithdraw` symmetric to the `requestWithdraw` post-default path.

### Proof of Concept
A Foundry fork PoC would: (1) deposit as attacker, `requestInstantWithdraw(B)`, have the CDO fund it via `getInstantWithdrawFunds`, then `claimInstantWithdrawRequest` (paid `B`); (2) leave another user's instant request unfunded, trigger borrower default and `finalizeDefaultRecovery`; (3) attacker submits a new `requestInstantWithdraw(B)`; (4) attacker calls `claimInstantWithdrawRequest` and receives `B * defaultRecoveryPrice` a second time from the reserve, leaving the last honest claimant unable to withdraw.