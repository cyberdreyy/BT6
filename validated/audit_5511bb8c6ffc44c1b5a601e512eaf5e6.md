### Title
Unprotected `initializeConfig` in `BridgeCommittee` Allows Any Caller to Inject Malicious Bridge Config — (`File: bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee.initializeConfig` is an `external` function with no access control. Its only guard is a "not yet set" check (`config == address(0)`). Any EOA that front-runs the legitimate deployer call can permanently bind the committee to an attacker-controlled `IBridgeConfig` contract, corrupting every downstream check that reads `committee.config()` — including chain-ID validation and token-address resolution inside `SuiBridge.transferBridgedTokensWithSignatures`.

---

### Finding Description

`BridgeCommittee.initializeConfig` is declared `external` with no `onlyOwner`, no committee-signature requirement, and no deployer check:

```solidity
// bridge/evm/contracts/BridgeCommittee.sol  lines 63-66
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
``` [1](#0-0) 

The deployment script calls this function in a **separate transaction** from the proxy deployment:

```solidity
// bridge/evm/script/deploy_bridge.s.sol  lines 174-178
BridgeCommittee(bridgeCommittee).initializeConfig(address(bridgeConfig));
BridgeCommittee committeeImplementation =
    BridgeCommittee(Upgrades.getImplementationAddress(bridgeCommittee));
committeeImplementation.initializeConfig(address(bridgeConfig));
``` [2](#0-1) 

Between the proxy deployment transaction and the `initializeConfig` transaction there is an open mempool window. Any observer can submit a higher-gas call to `initializeConfig` with an attacker-controlled `IBridgeConfig` address and win the race. Once set, the guard `require(address(config) == address(0), ...)` permanently blocks the legitimate call.

The injected `config` is consumed in `SuiBridge.transferBridgedTokensWithSignatures`:

```solidity
// bridge/evm/contracts/SuiBridge.sol  lines 67-82
IBridgeConfig config = committee.config();
...
require(tokenTransferPayload.targetChain == config.chainID(), "SuiBridge: Invalid target chain");
uint256 erc20AdjustedAmount = BridgeUtils.convertSuiToERC20Decimal(
    IERC20Metadata(config.tokenAddressOf(tokenTransferPayload.tokenID)).decimals(),
    config.tokenSuiDecimalOf(tokenTransferPayload.tokenID),
    tokenTransferPayload.amount
);
``` [3](#0-2) 

A malicious `IBridgeConfig` can:
- Return a wrong `chainID()`, causing every legitimate claim to revert on the chain-ID check.
- Return `address(0)` or a revert-on-call address from `tokenAddressOf`, making every token release revert.
- Return extreme decimal values from `tokenSuiDecimalOf`, causing arithmetic overflow or underflow in amount conversion.

All three outcomes permanently prevent users from claiming tokens that have already been burned/locked on the Sui side.

---

### Impact Explanation

Users who have already submitted a Sui→EVM bridge transfer (burning tokens on Sui) can never claim the corresponding ERC-20 tokens on Ethereum because every call to `transferBridgedTokensWithSignatures` reverts. The burned Sui tokens are gone; the EVM tokens are permanently locked in the vault. This is **permanent fund lock**, matching the High/Medium impact tier. If the malicious config is crafted to make `verifySignatures` accept forged chain-ID fields, it additionally constitutes a bridge governance bypass enabling illegitimate unlocks (Critical tier).

---

### Likelihood Explanation

The deployment script issues `initializeConfig` in a transaction that is broadcast to the public mempool after the proxy is already live. Any MEV bot or attacker watching for `BridgeCommittee` proxy deployments can detect the pending `initializeConfig` call and submit a competing transaction with higher gas. This is a standard front-running scenario with no technical barrier beyond gas cost.

---

### Recommendation

Remove the separate `initializeConfig` function entirely and pass `_config` as a parameter to the existing `initialize` function, setting it atomically inside the `initializer`-guarded call:

```solidity
function initialize(
    address[] memory committee,
    uint16[] memory stake,
    uint16 minStakeRequired,
    address _config          // add this
) external initializer {
    __CommitteeUpgradeable_init(address(this));
    __UUPSUpgradeable_init();
    // ... existing stake loop ...
    config = IBridgeConfig(_config);   // set atomically
}
```

If a two-step setup is unavoidable, restrict `initializeConfig` to the deployer:

```solidity
function initializeConfig(address _config) external onlyOwner {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

---

### Proof of Concept

1. Attacker watches the Ethereum mempool for a transaction that deploys a `BridgeCommittee` UUPS proxy.
2. Attacker deploys `MaliciousConfig` implementing `IBridgeConfig`:
   - `chainID()` returns `0xFF` (no valid chain will ever match).
   - `tokenAddressOf(id)` reverts unconditionally.
3. Attacker submits `BridgeCommittee(proxy).initializeConfig(address(maliciousConfig))` with gas price higher than the deployer's pending `initializeConfig` transaction.
4. Attacker's transaction is mined first; `config` is set to `MaliciousConfig`.
5. Deployer's `initializeConfig` reverts: `"BridgeCommittee: Config already initialized"`.
6. Any subsequent call to `SuiBridge.transferBridgedTokensWithSignatures` reverts at `require(tokenTransferPayload.targetChain == config.chainID(), ...)` because `MaliciousConfig.chainID()` returns `0xFF`.
7. All users who burned tokens on Sui cannot claim on Ethereum; funds are permanently locked.

### Citations

**File:** bridge/evm/contracts/BridgeCommittee.sol (L63-66)
```text
    function initializeConfig(address _config) external {
        require(address(config) == address(0), "BridgeCommittee: Config already initialized");
        config = IBridgeConfig(_config);
    }
```

**File:** bridge/evm/script/deploy_bridge.s.sol (L174-178)
```text
        // initialize config in the bridge committee
        BridgeCommittee(bridgeCommittee).initializeConfig(address(bridgeConfig));
        BridgeCommittee committeeImplementation =
            BridgeCommittee(Upgrades.getImplementationAddress(bridgeCommittee));
        committeeImplementation.initializeConfig(address(bridgeConfig));
```

**File:** bridge/evm/contracts/SuiBridge.sol (L67-82)
```text
        IBridgeConfig config = committee.config();

        BridgeUtils.TokenTransferPayload memory tokenTransferPayload =
            BridgeUtils.decodeTokenTransferPayload(message.payload);

        // verify target chain ID is this chain ID
        require(
            tokenTransferPayload.targetChain == config.chainID(), "SuiBridge: Invalid target chain"
        );

        // convert amount to ERC20 token decimals
        uint256 erc20AdjustedAmount = BridgeUtils.convertSuiToERC20Decimal(
            IERC20Metadata(config.tokenAddressOf(tokenTransferPayload.tokenID)).decimals(),
            config.tokenSuiDecimalOf(tokenTransferPayload.tokenID),
            tokenTransferPayload.amount
        );
```
