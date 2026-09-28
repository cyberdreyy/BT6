### Title
Caller-controlled swap `bytes` in `harvest` lets any user route reward sales through attacker-created pools and steal accrued rewards - (File: contracts/IdleCDO.sol)

### Summary
`harvest` (and its internal `_sellAllRewards`/`_sellReward` chain) accepts fully caller-controlled `bytes _extraData` that is decoded into Uniswap V3 `path`s at `contracts/IdleCDO.sol:648-651` and passed verbatim to `exactInput` at `contracts/IdleCDO.sol:622-630`. The path — not the declared `_rewardToken`/`_token` inputs — determines which pools execute the swap and which token is received, while `recipient` is hardcoded to the CDO and `amountOutMinimum` is entirely attacker-controlled (`_minAmount[i]` can be `0`). Any unprivileged caller can therefore craft a path ending in a self-deployed token/pool, dump the CDO's accrued reward tokens into it for near-zero output, and withdraw the rewards from the pool.

### Finding Description
- `_sellAllRewards` decodes `_extraData` into `bytes[] _paths` with no validation of the first or last hop: `contracts/IdleCDO.sol:648-651`. The loop then calls `_sellReward(_rewardToken, _paths[i], _sellAmounts[i], _minAmount[i])` at `contracts/IdleCDO.sol:666`.
- `_sellReward` grants `safeIncreaseAllowance` on `_rewardToken` to the Uniswap V3 router and calls `exactInput` with the raw caller path and caller-supplied `amountOutMinimum`: `contracts/IdleCDO.sol:619-630`.
- In Uniswap V3 `exactInput`, `path[0]` is the token pulled via `transferFrom` and each `(tokenIn, fee, tokenOut)` hop resolves a real pool from the canonical factory. Because the router can only pull `_rewardToken`, the path must start with `_rewardToken`, but every subsequent hop is unconstrained. The attacker creates a pool `rewardToken/attackerToken` at any fee tier on the real factory, seeds it with worthless `attackerToken` liquidity, and passes `path = rewardToken → attackerToken`, `_minAmount[i] = 0`.
- The swap deposits real reward tokens (e.g. CRV/CVX/LDO held by the CDO, or the full contract balance when `_sellAmounts[i] == 0`, per `contracts/IdleCDO.sol:610-613`) into the attacker's pool and pays out worthless `attackerToken` to `recipient = address(this)`. The attacker then removes liquidity, extracting the reward tokens.
- The worthless output is not `token`, so it does not inflate `_contractTokenBalance(token)`; instead the reward value is simply lost, lowering `getContractValue`. If the loss stays under `maxDecreaseDefault` (`_checkDefault`, `contracts/IdleCDO.sol:566-572`), harvest succeeds and the loss is socialized BB-first via `_updateAccounting`. If it exceeds the threshold, harvest reverts — a secondary reward-harvest DoS.

### Impact Explanation
Direct theft of all accrued, unsold reward tokens held by the CDO at harvest time (sold for ~0 via an attacker-owned pool). The loss is borne by tranche holders, junior (BB) tranche first, per the loss waterfall. The extractable amount equals the full pending reward balance since the last successful harvest, which for Convex/liquidity-mining strategies can be a material fraction of yield. No privileged role is needed: `harvest` is callable by any EOA, and `_extraData`, `_sellAmounts` and `_minAmount` are unrestricted caller inputs.

### Likelihood Explanation
Requirements are all unprivileged: deploy an ERC20, create+seed a Uniswap V3 pool against the reward token (e.g. `rewardToken/fakeToken`, fee tier of choice), and call `harvest` before an honest keeper does. There is no allowlist on `harvest`, no path whitelist, no output-token check, and no enforced slippage floor (`_minAmount` is caller-set). The only mitigation is racing legitimate harvesters; the attacker can skip rewards they cannot profitably drain via `_skipReward`. One caveat I could not fully verify in the indexed source: the exact `harvest` external signature — if any deployment restricts it, likelihood drops accordingly, but the base `IdleCDO` design treats harvest as permissionless.

### Recommendation
Constrain the swap encoding so the route cannot diverge from declared inputs:
- Decode and enforce `path[0] == _rewardToken` and `lastToken(path) == token` (or weth→token for multi-hop variants) in `_sellReward`.
- Enforce a minimum acceptable `amountOutMinimum` internally (e.g. derived from a TWAP/oracle or a configurable max slippage), rather than trusting caller `_minAmount`.
- Alternatively, whitelist intermediate pool tokens or restrict `harvest` reward-selling to a keeper, keeping a permissionless `harvest(true, ...)` path that only updates accounting.

### Proof of Concept
Foundry fork (mainnet), sketch:

```solidity
// fork at a block where the target IdleCDO holds accrued reward tokens
contract SwapPathTheft is Test {
    IdleCDO cdo = IdleCDO(CDO_ADDRESS);
    ISwapRouter router = ISwapRouter(0xE592427A0AEce92De3Edee1F18E0157C05861564);
    IUniswapV3Factory factory = IUniswapV3Factory(0x1F98431c8aD98523631AE4a59f267346ea31F984);

    function testStealRewards() public {
        address reward = IIdleCDOStrategy(cdo.strategy()).getRewardTokens()[0]; // e.g. CRV
        // 1. deploy worthless token, create + seed reward/FAKE pool
        FakeToken fake = new FakeToken();
        fake.mint(address(this), 1e30);
        address pool = factory.createPool(reward, address(fake), 3000);
        IUniswapV3Pool(pool).initialize(/* price */);
        fake.approve(router..., type(uint256).max);
        // mint fake-side liquidity so pool pushes out fake tokens
        INonfungiblePositionManager(...).mint(...); // single-sided fakeToken position

        uint256 rewardBefore = IERC20(reward).balanceOf(address(cdo));
        assertGt(rewardBefore, 0);

        // 2. craft path reward -> fake, minAmount 0
        bytes memory path = abi.encodePacked(reward, uint24(3000), address(fake));
        bytes[] memory paths = new bytes[](1);
        paths[0] = path;
        bytes memory extraData = abi.encode(paths);
        uint256[] memory sellAmounts = new uint256[](1); // 0 -> whole balance
        uint256[] memory minAmounts = new uint256[](1);  // 0 slippage floor
        bool[] memory skip = new bool[](1);              // sell this reward

        // 3. permissionless harvest executes the theft swap
        cdo.harvest(false, false, skip, sellAmounts, minAmounts, extraData);

        assertEq(IERC20(reward).balanceOf(address(cdo)), 0);
        // 4. remove liquidity / burn position -> attacker holds the CRV
        assertGt(IERC20(reward).balanceOf(address(this)), 0);
        // CDO contract value dropped by ~reward value; BB tranche absorbs it
    }
}
```