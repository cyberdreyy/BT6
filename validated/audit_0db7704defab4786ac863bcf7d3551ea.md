### Title
`depositDuringEpoch` is not covered by the pause mechanism — deposits continue while the vault is paused - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
The credit-vault pause mechanism relies on `Pausable._paused` to block user-initiated deposits (`_deposit` has `whenNotPaused`), while withdrawal paths are gated by dedicated `allowAAWithdrawRequest`/`allowBBWithdrawRequest`/`allowInstantWithdraw` flags. However, the mid-epoch deposit path `depositDuringEpoch` — an operation added on top of the ordinary deposit flow — checks only `isDepositDuringEpochDisabled`, `skipDefaultCheck`, `isProgrammableBorrower`, `isAYSActive`, epoch state and KYC. It never checks `paused()`. When the owner or guardian calls `pause()`/`emergencyShutdown()` during a running epoch to halt all user fund inflows, `depositDuringEpoch` remains callable and keeps routing user underlyings to the borrower and minting tranche tokens.

### Finding Description
In `IdleCDOEpochVariant._deposit` (line ~644) the ordinary deposit path is protected by `whenNotPaused`. `startEpoch` calls `_pause()`, and `emergencyShutdown()`/`_handleBorrowerDefault` also enforce `_pause()` (lines 246, 587-588, 603-605). Yet `depositDuringEpoch` (lines 656-669) contains no `paused()` check:

```solidity
function depositDuringEpoch(uint256 _amount, address _tranche) external returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      isProgrammableBorrower ||
      isAYSActive ||
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
    );
```

This mirrors the external bug class: the "opcode check" enumerates `isDepositDuringEpochDisabled`/`skipDefaultCheck` but omits the generic pause flag, so a new deposit op escapes the kill switch. The test at `test/foundry/IdleCreditVault.t.sol:5020-5038` confirms `emergencyShutdown` blocks `depositDuringEpoch` only through `skipDefaultCheck`; a plain `pause()` (guardian-callable, which does not set `skipDefaultCheck`) leaves the path open. `writeOffDeposit` similarly lacks a pause check but is borrower-restricted and out of attacker scope.

### Impact Explanation
During an incident response mid-epoch (e.g., a detected pricing/manipulation issue in the discounted mid-epoch mint math, oracle/strategy anomalies, or borrower distress), the guardian pauses the vault expecting all fund inflows to stop. An unprivileged KYC-passing attacker continues to call `depositDuringEpoch`, minting AA/BB tranche tokens at the discounted expected-final-NAV price and pushing underlying directly to the borrower (lines 730-732 `_transferUnderlyings(_borrower(), _amount)`). Consequences:

- Bypass of the security invariant "pause halts user fund entry", allowing the attacker to acquire tranche positions at a price computed from stale `expectedEpochInterest`/`lastNAV` after the team judged the state unsafe — diluting existing holders if the pause was triggered because interest expectations were mispriced.
- Fresh principal is irrevocably sent to the borrower for the rest of the epoch, converting an incident the team tried to freeze into continued exposure. If the epoch later defaults, the new funds join the defaulted basis but the attacker may have bought in at a discount while informed users were frozen out — asymmetric theft of yield relative to honest users who respected the pause.

Quantified loss: any attacker deposit `A` mints shares priced at `(expectedFinal + trancheInterest)/supply` while `expectedEpochInterest`/NAV may no longer reflect reality post-incident; the delta between the minted share value at epoch end and a fair price is a direct wealth transfer from existing LPs.

### Likelihood Explanation
Requires `isEpochRunning`, `isDepositDuringEpochDisabled == false`, non-programmable borrower, non-AYS deployment, and KYC allowlist pass — i.e., a mainnet epoch vault with mid-epoch deposits enabled and a guardian `pause()` (not `emergencyShutdown`, which sets `skipDefaultCheck`) during the epoch. Guardian pausing without full emergency shutdown is the documented lighter-weight response (`pause()` at line 518). Medium likelihood of the configuration + trigger coinciding; the missing check itself is unconditional in code.

### Recommendation
Add `paused()` to the `_checkNotAllowed` condition in `depositDuringEpoch` (or apply `whenNotPaused` semantics), so both `pause()` and `emergencyShutdown()` block all deposit paths. Audit any future user-facing entry point (e.g., new claim/request variants) to confirm it is covered by at least one of `paused()`, `skipDefaultCheck`, or the `allow*` flags, and add negative tests calling every entry point under `pause()` and `emergencyShutdown()`.

### Proof of Concept
Foundry fork scenario on `IdleCDOEpochVariant`:
1. User1 deposits AA during buffer; owner calls `setIsAYSActive(false)`, `setIsDepositDuringEpochDisabled(false)`; manager calls `startEpoch()` (vault now `paused()`).
2. Guardian calls `pause()` (or owner `emergencyShutdown` without epoch stop — for `pause` alone `skipDefaultCheck` stays false).
3. Attacker (fresh KYC'd wallet) calls `depositDuringEpoch(amount, AAtranche)` — succeeds, mints discounted tranche tokens, underlyings transferred to borrower.
4. Assert `cdoEpoch.paused() == true` while `attacker` AA balance > 0 and borrower received funds — the pause failed to block the deposit op.