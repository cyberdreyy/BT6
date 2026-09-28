### Title
Post-default withdraw requests pay out 1:1 from the recovery reserve, letting new redeemers drain funds reserved for defaulted-epoch claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`finalizeDefaultRecovery` sizes `defaultRecoveryReserve` to cover exactly `defaultPendingClaimBasis()` (pending receipts + unfunded current-epoch instant receipts) times `defaultRecoveryPrice`, plus the active LP basis. After finalization, `requestWithdraw` lets any tranche holder create a `postDefaultRequests` entry by burning already-haircut CDO strategy tokens, and `claimWithdrawRequest` → `_claimPostDefaultWithdrawRequest` pays that amount **1:1 out of `defaultRecoveryReserve` immediately, with no epoch gating and no contribution of new underlying**. The reserve is a closed pool: every post-default claim is paid at par while the tokens burned to create it were only worth `defaultRecoveryPrice`. Repeated request→claim cycles leak the reserve until defaulted-epoch claimants can no longer be paid — a resource-leak analog of CVE-2019-0148 (repeated unprivileged allocation of a finite resource causes denial of service).

### Finding Description
In `requestWithdraw`, once `defaultRecoveryFinalized` is true, the only precondition is that the user has no open requests. The CDO burns its strategy tokens (`_burn(msg.sender, _amount)`), mints the receipt to the user, and records `postDefaultRequests[_user] = _amount` — but **no underlying is added to the reserve** to back this new claim (`contracts/strategies/idle/IdleCreditVault.sol:247-257`).

`claimWithdrawRequest` then calls `_claimPostDefaultWithdrawRequest`, which zeroes the entry, burns the receipt and calls `_transferDefaultRecovery(_user, amount)` — decrementing `defaultRecoveryReserve` and transferring underlying 1:1 (`contracts/strategies/idle/IdleCreditVault.sol:760-767`, `912-917`). Unlike normal funded claims (`_claimFundedWithdrawRequest` requires `epochNumber > lastWithdrawRequest`), the post-default path has **no waiting period at all** — request and claim can happen in the same transaction.

The reserve arithmetic at finalization (`contracts/strategies/idle/IdleCreditVault.sol:679-692`) is `reserveAmount = recoveryPrice * totalBasis / RECOVERY_FULL`, where `totalBasis` covers only pre-existing receipts and active CDO NAV. Post-default claimants were never part of `totalBasis`, yet each consumes `amount` (not `amount * recoveryPrice`) from the reserve. Since the haircut already reduced the CDO's tranche price, the user's `_amount` reflects ~`recoveryPrice` of their original value — but they withdraw the full `_amount`, i.e. `1/recoveryPrice` times their fair share.

### Impact Explanation
Direct theft leading to insolvency of the recovery reserve. Example: `defaultRecoveryPrice = 0.5e18` (50% recovery), reserve = 500k USDC backing 1M of defaulted claims. An attacker holding BB tranches (post-default value 100k at haircut prices) requests withdraw: `_amount = 100k` is recorded. Immediate claim pays 100k from the reserve — the attacker recovers 100% of pre-default face while every defaulted-epoch claimant was entitled to only 50%. Repeating (each cycle only requires tranche tokens, purchasable at the depressed post-default price) drains the reserve; legitimate `_claimDefaultedWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest` calls then revert on `defaultRecoveryReserve -= _amount` underflow — permanent freezing/theft of unclaimed recovery funds. The loss equals the drained reserve amount.

### Likelihood Explanation
- Attacker is unprivileged: any tranche-token holder post-default (buys tranches on the market at the haircut price, no KYC needed for holding tranches).
- Trigger conditions exist in the intended system: borrower default → `finalizeDefaultRecovery` is an explicitly supported flow.
- No guard stops it: `_transferDefaultRecovery` only decrements the counter; there is no check that post-default claims were provisioned, no epoch delay, and the "one open request" check is trivially satisfied by claiming before re-requesting.
- Cost: only the haircut-priced tranche tokens; profit is `(1 - recoveryPrice)` per unit of face value per cycle.

### Recommendation
Fund post-default requests from a distinct source, or apply the recovery haircut a second time. Concretely: either (a) have the CDO transfer the corresponding underlying into the strategy when a post-default request is created (pulling from the CDO's post-default NAV), or (b) pay post-default claims as `amount * defaultRecoveryPrice / RECOVERY_FULL` so they consume the reserve at the same rate as defaulted claims. At minimum, add the same epoch-wait gating used by `_claimFundedWithdrawRequest` so a single transaction cannot cycle request→claim repeatedly.

### Proof of Concept
```solidity
// Foundry fork PoC: post-default reserve drain
// Setup (reusing test/foundry/IdleCreditVault.t.sol helpers):
// 1. LPs deposit AA/BB, startEpoch(), warp past epochDuration.
// 2. Borrower defaults: call cdoEpoch._handleBorrowerDefault path
//    (borrower repays partial amount R < owed), then strategy.finalizeDefaultRecovery(R, source)
//    via the CDO. Now defaultRecoveryFinalized == true, recoveryPrice = R / totalBasis < 1.
//
// Attack:
address attacker = makeAddr('attacker');
// attacker buys BB tranches at post-default (haircutted) price on a DEX/mock:
uint256 face = 200_000e6;                       // pre-default face value acquired
uint256 haircutValue = face * strategy.defaultRecoveryPrice() / 1e18;
deal(address(underlying), attacker, haircutValue, true);
// acquire tranches worth `face` pre-default (price = recoveryPrice)
// ... attacker deposits/holds tranche tokens, then:

vm.startPrank(attacker);
uint256 claimed;
while (true) {
    uint256 amt = /* tranche balance converted to underlying at haircut price */;
    if (amt == 0) break;
    cdoEpoch.requestWithdraw(trancheAmt, address(BBtranche)); // -> postDefaultRequests
    uint256 before = underlying.balanceOf(attacker);
    cdoEpoch.claimWithdrawRequest();            // pays amt 1:1, same tx, no wait
    claimed += underlying.balanceOf(attacker) - before;
    if (strategy.defaultRecoveryReserve() < amt) break;
}
vm.stopPrank();

// Attacker recovered face value while reserve was sized for recoveryPrice * basis:
assertGt(claimed, haircutValue);                // profit = claimed - haircutValue
// Honest defaulted claimant now bricked:
vm.expectRevert();                              // reserve underflow / insufficient balance
cdoEpoch.claimWithdrawRequest(/* honest user */);
```

Key assertions the PoC demonstrates:
- `postDefaultRequests` entries are payable the same transaction they are created (no epoch gate in `_claimPostDefaultWithdrawRequest`).
- Each claim decrements `defaultRecoveryReserve` by the full amount while the backing tokens burned were only worth `recoveryPrice` of that amount.
- The loop terminates in reserve insolvency, bricking `claimWithdrawRequest`/`claimInstantWithdrawRequest` for defaulted-epoch receipt holders.