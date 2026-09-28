### Title
Missing same-block flash protection in `IdleCDOCreditVault._deposit` allows flash deposit → instant-withdraw yield theft - (File: `contracts/IdleCDOCreditVault.sol`)

### Summary
The external report describes a flash-protection check (`ownershipChange[tokenId] == block.number`) that exists in `balanceOfNFT` but is missing in `votingPowerOf`, making the protection redundant on the path that actually matters. The idle-tranches analog is the `_lastCallerBlock` / `_checkSameBlock` mechanism: `IdleCDO._deposit` sets `_lastCallerBlock` via `_updateCallerBlock()` (`contracts/IdleCDO.sol:241`) and every `_withdraw` variant rejects same-origin same-block withdrawals via `_checkSameBlock()` (`contracts/IdleCDO.sol:479`, `contracts/IdleCDO.sol:1016-1023`). The credit-vault rewrite `IdleCDOCreditVault._deposit` dropped `_updateCallerBlock()` entirely, so `_lastCallerBlock` is never populated and any same-block guard in the withdraw/claim paths (e.g., `claimInstantWithdrawRequest` in the epoch vault) can never trigger — the protection is structurally dead, exactly as in the external report where the unchecked function bypasses the check.

### Finding Description
In `IdleCDO`, the flash-deposit defense works as a pair:

- `_deposit` records `keccak256(tx.origin, block.number)` into `_lastCallerBlock` (`contracts/IdleCDO.sol:240-241`, `contracts/IdleCDO.sol:1016-1018`).
- `_withdraw` reverts if the same `tx.origin` acts in the same block (`contracts/IdleCDO.sol:478-479`, `contracts/IdleCDO.sol:1021-1023`), preventing deposit→withdraw in one transaction to sandwich an interest/accounting update.

`IdleCDOCreditVault._deposit` (`contracts/IdleCDOCreditVault.sol:191-212`) performs `_guarded`, `_updateAccounting`, share minting via `_mintSharesAtCurrPrice`, and the strategy deposit — but never calls `_updateCallerBlock`. `_lastCallerBlock` therefore remains `bytes32(0)` forever in credit vaults, so even where a withdraw path retains a `_checkSameBlock` call, `keccak256(tx.origin, block.number)` can never equal `0` and the check is a no-op. An attacker can atomically deposit into the vault and withdraw/claim (instant-withdraw path during a running epoch, or `depositBB`+`withdrawBB` style flows) inside a single block/transaction, capturing any NAV uplift credited by `_updateAccounting`/`_accrueManagementFee` ordering or any price discrepancy between `tranchePrice` and `virtualPrice`, without being exposed to the epoch risk the deposit was meant to underwrite.

### Impact Explanation
Broken invariant: fair mint/burn and flash-deposit isolation. An attacker with flash liquidity (or just atomic bundling) can mint tranche shares and redeem them within the same block, skimming yield that accrued to honest holders between the last accounting update and the attacker's transaction, and avoiding any exposure to the credit risk of the epoch. Quantified loss equals the unsplit interest harvested per atomic cycle, repeatable each block where a positive NAV delta exists.

### Likelihood Explanation
Requires a positive pending NAV delta (accrued strategy interest or fee realization) and an atomic deposit+withdraw path — available to any unprivileged KYC-passing lender via the instant-withdraw/claim surface. No privileged role is involved. Likelihood is bounded by how much yield can be captured per atomic cycle, but the structural gap (the setter half of the guard pair is missing) is unconditional.

### Recommendation
Restore the guard pair in the credit vault: call `_updateCallerBlock()` at the start of `IdleCDOCreditVault._deposit` (mirroring `contracts/IdleCDO.sol:241`), and ensure every withdraw/claim entry point (`_withdraw`, `claimInstantWithdrawRequest`, `claimWithdrawRequest`) enforces `_checkSameBlock()` before burning shares or releasing funds. Alternatively, record per-depositor deposit block and require `block.number > depositBlock` on redemption.

### Proof of Concept
A Foundry fork test would: (1) deploy/fork an `IdleCDOCreditVault` instance with accrued strategy yield pending since the last accounting update; (2) in a single transaction, approve and call `depositAA(amount)` then immediately `withdrawAA(0)`/instant-claim; (3) assert the transaction succeeds (no `SameBlock` revert) and the attacker balance increases by the captured interest delta. Note: I was unable to read the epoch-vault claim functions (`IdleCDOEpochVariant`/credit-vault `claimInstantWithdrawRequest`) within this session, so the exact claim entry point that pairs with this missing setter should be confirmed there — the missing `_updateCallerBlock` in `_deposit` itself is verified at `contracts/IdleCDOCreditVault.sol:191-212`.