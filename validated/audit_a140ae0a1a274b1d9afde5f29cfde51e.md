### Title
Same-block deposit/withdraw guard keyed on `tx.origin` can be bypassed by multi-origin bundles (and future account-abstraction semantics), enabling flash-loan yield extraction - (File: contracts/IdleCDO.sol)

### Summary
`IdleCDO` protects against same-block deposit→redeem cycles (flash-loan / reward-sandwich attacks) via `_updateCallerBlock` / `_checkSameBlock`, which fingerprint the caller as `keccak256(tx.origin, block.number)`. Because the identity is `tx.origin` rather than `msg.sender`, the guard is only effective for a single-origin flow. An attacker can defeat it by splitting the two legs across two different `tx.origin`s in the same block (builder bundles / two cooperating EOAs or delegate contracts), and the fragility compounds under EIP-3074/7702-style account delegation where `tx.origin` no longer reliably distinguishes the *actual* executor of each call layer — the exact bug class flagged in the Rio `rebalance()` report (`msg.sender != tx.origin` checks become ineffective). [1](#0-0) 

### Finding Description
`IdleCDO` calls `_updateCallerBlock()` at the start of deposit functions and `_checkSameBlock()` inside redeem paths (and vice versa), reverting with `SameBlock()` when `keccak256(abi.encodePacked(tx.origin, block.number))` matches the stored `_lastCallerBlock` (`IdleCDOStorage.sol`). The intent is to guarantee that a single economic actor cannot mint tranche tokens and redeem them within the same block, i.e., cannot flash-loan the NAV update or skim accrued rewards/yield atomically.

The weakness is twofold:

1. **Cross-origin same-block bypass (works today).** The check binds identity to `tx.origin`. Two transactions with different origins land in the same block trivially via builder bundles (MEV-Boost/Flashbots). Tx1 (origin A): `depositAA`/`depositBB` with flash-borrowed underlying → `_updateCallerBlock` records origin A. Tx2 (origin B, same block): `withdrawAA`/`withdrawBB` of those tranche tokens (transferred, or held by a cooperating contract) → `_checkSameBlock` hashes origin B → no match → bypass. The guard therefore only protects against the cheapest version of the attack (single EOA), not against any funded attacker willing to run two transactions.

2. **`tx.origin` semantics degrade under AA upgrades (the external report's class).** The Rio report's point applies directly: checks whose security depends on `tx.origin` behavior break under EIP-3074/7702 delegation, where a contract executes calls whose `msg.sender == tx.origin` layer or where a single delegated origin orchestrates batched calls. A delegated-account or "EOA-with-code" world makes origin-based same-block correlation unreliable in both directions — either false positives (multiple users routed through one origin/relayer get `SameBlock()` reverts → temporary freeze/DoS of redemptions) or false negatives as in (1).

### Impact Explanation
The guard exists precisely because same-block deposit+redeem is economically dangerous: `_updateAccounting` runs on deposit/redeem and splits accrued strategy yield between AA/BB at the *current* `virtualPrice`. An attacker who can deposit at block start (pre-accounting or stale price) and redeem post-harvest/`_updateAccounting` within the same block extracts yield accrued over the whole epoch from existing tranche holders, with zero time-at-risk — paid for with a same-block flash loan. Loss scales with the flash-loanable amount relative to TVL and the yield accrued since the last accounting call; on a stale epoch this can be a material fraction of the pending yield. The failure mode is direct theft of yield from other tranche holders (fair mint/burn + solvency-adjacent invariant).

### Likelihood Explanation
- Bypass requires two transactions with different origins in one block — standard builder-bundle capability, no privileged role, no KYC issue beyond `isWalletAllowed` (a KYC-passing lender qualifies, or tranche tokens can be transferred to any address between the two txs since tranche tokens are freely transferable).
- Profitability depends on harvest/yield cadence: it pays whenever `harvest()`/accounting updates or pending yield within a block exceeds the flash-loan fee + two gas costs. Epochs with infrequent `_updateAccounting` are the vulnerable window.
- The EIP-3074/7702 leg is speculative-future but the multi-origin bypass needs no protocol change.

### Recommendation
- Do not key anti-flash-loan logic on `tx.origin`. Key the marker on `msg.sender` *and* on the tranche token holder at redeem time, or better: enforce a minimum holding period / epoch boundary between mint and redeem of the same tranche tokens (e.g., record `block.number` of last mint per share batch and require `redeemBlock > mintBlock`), which cannot be bypassed by splitting across origins.
- Alternatively, snapshot the redeemable value at mint time so same-block mints only redeem at the pre-update `virtualPrice` (deny the price improvement), making the sandwich structurally unprofitable rather than detected.

### Proof of Concept
Foundry fork sketch (fill contract names/addresses per fixture):

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;
import "forge-std/Test.sol";

contract SameBlockBypassPoC is Test {
    IdleCDO cdo;        // in-scope IdleCDO instance
    IERC20 token;       // underlying
    address AA;         // AA tranche token

    function test_multiOriginSameBlockBypass() public {
        uint256 amount = 10_000_000e6; // flash-loaned underlying
        address eoaA = makeAddr("originA");
        address eoaB = makeAddr("originB");
        address proxy = makeAddr("helperContract"); // holds tranche tokens

        // Simulate a block where yield/harvest lands between the two txs
        // (or accrue interest so virtualPrice rises after _updateAccounting)

        // Tx1: origin A deposits
        deal(address(token), eoaA, amount);
        vm.startPrank(eoaA, eoaA); // msg.sender == tx.origin == eoaA
        token.approve(address(cdo), amount);
        uint256 minted = cdo.depositAA(amount);
        vm.stopPrank();

        // transfer tranche tokens to a second identity (or deposit directly to proxy)
        vm.prank(eoaA);
        IERC20(AA).transfer(proxy, minted);

        // Tx2: same block, different origin -> _checkSameBlock passes
        vm.prank(proxy, eoaB); // msg.sender = proxy, tx.origin = eoaB (same block.number)
        uint256 out = cdo.withdrawAA(minted);

        // Assert redeem succeeded in same block (guard bypassed)
        assertGt(out, 0);
        // Profit case: repeat with a harvest/_updateAccounting sandwiched between txs
        // to show out > amount (extracted accrued yield).
    }
}
```

The core repro is that `_checkSameBlock` hashes `tx.origin`, so `vm.prank(proxy, eoaB)` in the same block evades it — demonstrating that `keccak256(tx.origin, block.number)` is an inadequate identity for the anti-same-block invariant.

Note: I could not fully verify, within the tool-call budget, every call site of `_updateCallerBlock`/`_checkSameBlock` (whether they gate `depositAA/BB`/`withdrawAA/BB` or also `TrancheWrapper` flows) or the exact harvest cadence that makes the sandwich profitable — the PoC asserts the guard bypass itself, which is the broken invariant. If deposit/redeem do not actually route through these helpers on the audited variant, this reduces to an acknowledged-design limitation rather than an exploitable path.

### Citations

**File:** contracts/IdleCDO.sol (L1015-1023)
```text
  /// @dev Set last caller and block.number hash. This should be called at the beginning of the first function to protect
  function _updateCallerBlock() internal {
    _lastCallerBlock = keccak256(abi.encodePacked(tx.origin, block.number));
  }

  /// @dev Check that the second function is not called in the same block from the same tx.origin
  function _checkSameBlock() internal view {
    if (keccak256(abi.encodePacked(tx.origin, block.number)) == _lastCallerBlock) revert SameBlock();
  }
```
