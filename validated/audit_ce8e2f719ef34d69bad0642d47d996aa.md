### Title
`harvest` trusts attacker-controlled array lengths and zero min-amounts, allowing theft of accrued rewards via min-0 swaps - (File: contracts/IdleCDO.sol)

### Summary
The external bug (CVE-2026-34462) is an unchecked variable-length copy: a handler copies an attacker-supplied buffer into a fixed-size destination without verifying termination/length, and does so before authorization. The idle-tranches analog is `IdleCDO.harvest` → `_sellAllRewards`: any caller supplies `_extraData`/`_skipReward`/`_sellAmounts`/`_minAmount` and the code indexes and decodes them against the strategy's `_rewards.length` without validating array lengths, and forwards `_minAmount[i]` directly as the Uniswap `amountOutMinimum`. An attacker can call `harvest` with all min-amounts set to 0 and sandwich the reward swaps, stealing accrued reward value before it is compounded into the tranche NAV.

### Finding Description
In `IdleCDO.sol`, `_sellAllRewards` iterates `for (uint256 i; i < rewardsLen; ++i)` over `rewardsLen = _rewards.length` and reads caller-supplied parallel arrays `_skipReward[i]`, `_sellAmounts[i]`, `_minAmount[i]`, and `_paths[i]` decoded from `_extraData` — none of which is checked to have `length >= rewardsLen` and no check that `_minAmount[i] > 0` [1](#0-0) . The decoded `_paths` array is assigned wholesale with `if (_extraData.length > 0) { _paths = abi.decode(_extraData, (bytes[])); }` [2](#0-1) . The analogous pattern exists in `MetaMorphoStrategy.redeemRewards`, which iterates `_rewardsLen` and indexes `claimDatas[i]` decoded from caller `data` with no length check [3](#0-2) , and in `ConvexBaseStrategy.redeemRewards`, which indexes caller-decoded `_skipSell[i]` / `_minAmountsWETH[i]` over `_convexRewards.length` [4](#0-3) .

Two distinct consequences:
1. **Length mismatch → revert**: if the supplied arrays are shorter than `rewardsLen`, indexing reverts. This alone is only a DoS of that harvest call and is not fund-impacting by itself.
2. **Zero min-amount → extractable swap**: `_sellReward` passes `_minAmount` straight into `ISwapRouter.ExactInputParams.amountOutMinimum` [5](#0-4) . Since `harvest` is permissionless and the caller chooses all swap parameters, an attacker who observes accrued reward balances can invoke harvest with `_minAmount[i] = 0` and a path they control, then sandwich the swap — converting reward tokens that belong to tranche holders (they would otherwise be compounded into `priceAA`/`priceBB`) into MEV profit.

### Impact Explanation
Direct theft of unclaimed yield. The attacker is an unprivileged EOA (harvest has no access control). The loss equals the reward-token balances sold at min-0; for a strategy holding reward tokens (e.g., CRV/CVX via Convex strategies, or rewards in `MetaMorphoStrategy`), the entire accrued-but-unsold reward value can be extracted, reducing `getContractValue` and tranche prices. Broken invariant: fair yield distribution — rewards owed to AA/BB tranche holders are diverted to the caller/MEV. Existing guards do not stop it: the only check on `_minAmount` is its presence in the array; there is no floor (e.g., an oracle-priced minimum) and no caller allowlist.

### Likelihood Explanation
Likelihood is moderate-to-high whenever reward balances are non-trivial and the reward-token liquidity path is sandwichable. It requires no privileged role, no epoch timing, and no victim interaction — the attacker supplies all hostile inputs in one transaction. The main caveat: honest keepers usually harvest promptly, so the window is limited to periods with meaningful accrued-but-unsold rewards; and if this exact min-amount pattern is already documented as accepted harvest-caller responsibility in prior audits, it may be a previously acknowledged issue (uncertain — I could not fully confirm audit scope from the index).

### Recommendation
- Enforce `_sellAmounts.length == _minAmount.length == _skipReward.length == rewardsLen` (and `_paths.length == rewardsLen` when `_extraData` is provided) before the loop.
- Replace trust in caller-supplied `amountOutMinimum` with an on-chain floor: derive a min-out from a TWAP/Chainlink price for each reward token, or restrict `harvest` to a whitelisted keeper set.
- Same bounds checks in `MetaMorphoStrategy.redeemRewards` (`claimDatas.length == _rewardsLen`) and `ConvexBaseStrategy.redeemRewards` (decoded array lengths vs `_convexRewards.length`).

### Proof of Concept
Foundry fork test (mainnet), against a CDO whose strategy holds reward tokens:

```solidity
// test/foundry/HarvestMinAmountTheft.t.sol
function testHarvestMinZeroSandwich() public {
    // 1. Deposit into the CDO and warp so rewards accrue on the strategy.
    idleCDO.depositAA(100_000 * ONE_SCALE);
    // ... advance time / poke convex rewards so strategy has CRV/CVX balance ...

    // 2. Record victim price before hostile harvest
    uint256 priceAABefore = idleCDO.virtualPrice(address(AAtranche));

    // 3. Attacker EOA calls harvest with:
    //    _sellAmounts[i] = 0 (sell whole balance), _minAmount[i] = 0,
    //    _skipReward[i] = false, _extraData = abi.encode(paths to a shallow pool)
    uint256 rewardsLen = strategy.getRewardTokens().length;
    uint256[] memory sellAmounts = new uint256[](rewardsLen); // 0 = sell all
    uint256[] memory minAmounts = new uint256[](rewardsLen);  // 0 min-out
    bool[] memory skip = new bool[](rewardsLen);              // all false
    bytes memory extraData = abi.encode(attackerPaths);

    // 4. Sandwich: attacker dumps reward token price, calls harvest (swaps at min 0),
    //    buys back, pocketing the spread.
    vm.prank(attacker);
    idleCDO.harvest(skip, true, minAmounts, sellAmounts, extraData);

    // 5. Rewards were sold for far less than market: tranche NAV is lower than
    //    it would be after an honest harvest.
    assertLt(idleCDO.virtualPrice(address(AAtranche)), expectedPriceAfterFairHarvest);
}
```

The length-mismatch variant is shown by passing `minAmounts` of length `rewardsLen - 1`: the call panics on `_minAmount[i]` at the last index [6](#0-5) , demonstrating the missing bounds validation that mirrors the unterminated-copy bug class.

### Citations

**File:** contracts/IdleCDO.sol (L622-630)
```text
    ISwapRouter.ExactInputParams memory params = ISwapRouter.ExactInputParams({
      path: _path,
      recipient: address(this),
      deadline: block.timestamp + 100,
      amountIn: _amount,
      amountOutMinimum: _minAmount
    });
    // do the swap and return the amount swapped and the amount received
    return (_amount, _swapRouter.exactInput(params));
```

**File:** contracts/IdleCDO.sol (L641-668)
```text
  function _sellAllRewards(IIdleCDOStrategy _strategy, uint256[] memory _sellAmounts, uint256[] memory _minAmount, bool[] memory _skipReward, bytes memory _extraData)
    internal virtual
    returns (uint256[] memory _soldAmounts, uint256[] memory _swappedAmounts, uint256 _totSold) {
    // Fetch state variables once to save gas
    // get all rewards addresses
    address[] memory _rewards = _strategy.getRewardTokens();
    address _rewardToken;
    bytes[] memory _paths = new bytes[](_rewards.length);
    if (_extraData.length > 0) {
      _paths = abi.decode(_extraData, (bytes[]));
    }
    uint256 rewardsLen = _rewards.length;
    // Initialize the return array, containing the amounts received after swapping reward tokens
    _soldAmounts = new uint256[](rewardsLen);
    _swappedAmounts = new uint256[](rewardsLen);
    // loop through all reward tokens
    for (uint256 i; i < rewardsLen; ++i) {
      _rewardToken = _rewards[i];
      // check if it should be sold or not
      if (_skipReward[i]) { continue; }
      // do not sell stkAAVE but only AAVE if present
      if (_rewardToken == stkAave) {
        _rewardToken = AAVE;
      }
      // Market sell _rewardToken in this contract for _token
      (_soldAmounts[i], _swappedAmounts[i]) = _sellReward(_rewardToken, _paths[i], _sellAmounts[i], _minAmount[i]);
      _totSold += _swappedAmounts[i];
    }
```

**File:** contracts/strategies/morpho/MetaMorphoStrategy.sol (L100-113)
```text
    bytes[] memory claimDatas = abi.decode(data, (bytes[]));
    address cdo = idleCDO;
    address reward;
    address rewardDistributor;
    uint256 claimable; 
    bytes32[] memory proof;

    for (uint256 i = 0; i < _rewardsLen; i++) {
      if (claimDatas[i].length == 0) {
        continue;
      }
      (reward, rewardDistributor, claimable, proof) = abi.decode(claimDatas[i], (address, address, uint256, bytes32[]));
      rewards[i] = _claimReward(IUniversalRewardsDistributor(rewardDistributor), cdo, reward, claimable, proof);
    }
```

**File:** contracts/strategies/convex/ConvexBaseStrategy.sol (L282-296)
```text
        uint256[] memory _minAmountsWETH = new uint256[](_convexRewards.length);
        bool[] memory _skipSell = new bool[](_convexRewards.length);
        uint256 _minDepositToken;
        uint256 _minLpToken;
        (_minAmountsWETH, _skipSell, _minDepositToken, _minLpToken) = abi.decode(_extraData, (uint256[], bool[], uint256, uint256));

        IBaseRewardPool(rewardPool).getReward();

        address _reward;
        IERC20Detailed _rewardToken;
        uint256 _rewardBalance;
        IUniswapV2Router02 _router;

        for (uint256 i = 0; i < _convexRewards.length; i++) {
            if (_skipSell[i]) continue;
```
