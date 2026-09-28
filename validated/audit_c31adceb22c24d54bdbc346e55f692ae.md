### Title
Fee-on-transfer underlying inflates strategy-token NAV via flat-amount forward deposit - (File: contracts/IdleCDOCreditVault.sol)

### Summary
The Timeswap bug class — a contract forwarding a flat `_amount` to a downstream contract that credits the flat amount while actually receiving less due to transfer fees — maps onto the credit-vault deposit path. `IdleCDOCreditVault._deposit` correctly mints tranche shares from the *received* balance delta, but then calls `IIdleCDOStrategy(strategy).deposit(_amount)` with the original flat `_amount`. If `token` charges a fee on transfer, the strategy receives less than `_amount` yet mints strategy tokens for the full `_amount`, inflating `getContractValue()` and letting the depositor withdraw more than they contributed.

### Finding Description
In `IdleCDOCreditVault._deposit` (contracts/IdleCDOCreditVault.sol:191-212):

```solidity
uint256 _preBal = _contractTokenBalance(_token);
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
_minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
...
IIdleCDOStrategy(strategy).deposit(_amount);
```

The tranche mint uses the balance delta, so a fee on the first transfer only reduces the attacker's minted shares — that part is safe. The defect is the unconditional `strategy.deposit(_amount)` at line 211, which reuses the pre-fee parameter.

Strategies pull funds with `safeTransferFrom(msg.sender, address(this), _amount)` and mint shares priced on the flat `_amount` — e.g., `BaseStrategy.deposit` (contracts/strategies/BaseStrategy.sol:124-142) computes `shares = (_amount * EXP_SCALE) / _price` from the nominal amount, and credit-vault strategy tokens are minted 1:1 with underlyings (per `getContractValue`, contracts/IdleCDOCreditVault.sol:125-128, which counts `_contractTokenBalance(strategyToken)` at face value). The same flat-forward exists in `IdleCDO._deposit` under `directDeposit` (contracts/IdleCDO.sol:257-259).

Attack sequence (running epoch, normal mode):
1. Attacker calls `depositAA(X)` with a fee-on-transfer `token` (fee rate f). The CDO receives `X(1-f)`; attacker is minted AA shares worth `X(1-f)` at current price — fair so far.
2. `_deposit` calls `strategy.deposit(X)`. Provided the CDO holds at least `X` in idle balance (e.g., liquidity buffer from prior deposits/harvests, or undeposited funds), the `transferFrom` succeeds; the strategy receives `X(1-f)` but mints `X` strategy tokens to the CDO.
3. `getContractValue()` now overstates NAV by `X·f` (strategy tokens counted 1:1, contracts/IdleCDOCreditVault.sol:127).
4. Attacker calls `withdrawAA` and redeems against the inflated NAV, extracting value backed by other tranche holders.

Repeatable each deposit; no privileged action required — only an honest buffer of idle underlying in the CDO.

### Impact Explanation
Direct theft / solvency break: each deposit inflates the strategy-token-denominated NAV by the transfer fee (`X·f`), which is socialized across all tranche holders when the attacker withdraws. Loss per iteration equals the token's transfer fee on the deposited amount; unbounded by repetition. The fair-mint and solvency invariants are broken because strategy shares are minted against a nominal amount rather than the actual received balance.

### Likelihood Explanation
Requires the configured `token` to be fee-on-transfer (or to adopt a fee, as USDT/USDC can). The CDOs are deployed per-token, so this only affects vaults whose underlying takes fees — but nothing in the code prevents or detects such a token, and the received-vs-nominal asymmetry is unconditional. Where the CDO holds no idle buffer, the second `transferFrom` under-funds the strategy (revert or silent shortfall depending on token semantics), so the inflation path needs pre-existing CDO balance — common in normal operation.

### Recommendation
Measure the strategy-side deposit by balance delta, or forward only the received amount:

```solidity
uint256 _received = _contractTokenBalance(_token) - _preBal;
_minted = _mintSharesAtCurrPrice(_received, msg.sender, _tranche);
IIdleCDOStrategy(strategy).deposit(_received);
```

Alternatively, have `strategy.deposit` compute minted shares from its own `balanceOf` delta (as `IdleClearpoolStrategy._depositToVault` already does for cpTokens at contracts/strategies/clearpool/IdleClearpoolStrategy.sol:259-267) and document that fee-on-transfer/rebasing underlyings are unsupported.

### Proof of Concept
Foundry fork sketch (credit vault, fee-on-transfer mock or a fee-enabled stable):

```solidity
// token charges 1% on every transfer
uint256 X = 1_000_000e6;
uint256 fee = X / 100;

// seed CDO with an idle buffer >= X from honest LP (or prior harvest)
vm.prank(honestLP); cdo.depositAA(2 * X); // leaves idle underlying in CDO

uint256 navBefore = cdo.getContractValue();
uint256 cdoStratBalBefore = strategyToken.balanceOf(address(cdo));

vm.prank(attacker); cdo.depositAA(X);
// strategy received X-fee but minted X strategy tokens
assertEq(strategyToken.balanceOf(address(cdo)) - cdoStratBalBefore, X);
// underlying actually held by strategy only grew by X-fee
assertEq(underlying.balanceOf(address(strategy)), X - fee);

// NAV overstated by `fee`; attacker withdraws more than X-fee contributed
vm.prank(attacker); cdo.withdrawAA(attackerShares);
assertGt(underlying.balanceOf(attacker) - attackerBefore, X - fee);
```

Note: I could not fully verify the exact mint path inside `contracts/strategies/idle/IdleCreditVault.sol` (the likely strategy for credit vaults) within available context; the claim relies on the documented 1:1 mint in `getContractValue` and the flat-amount `deposit` call pattern shared with `BaseStrategy`.