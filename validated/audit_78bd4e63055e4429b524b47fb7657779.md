### Title
Unprotected `initializeConfig()` Allows Any Caller to Permanently Hijack Bridge Config — (`File: bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee.initializeConfig()` carries no access-control modifier. Any address can call it before the legitimate deployer does and permanently install a malicious `IBridgeConfig` contract. The "already initialized" guard then prevents the real config from ever being set.

---

### Finding Description

`BridgeCommittee` is deployed as a UUPS proxy. Its setup requires two separate transactions:

1. `initialize(committee, stake, minStakeRequired)` — protected by OpenZeppelin's `initializer` modifier.
2. `initializeConfig(_config)` — **completely unprotected**. [1](#0-0) 

```solidity
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

The function is `external` with no `onlyOwner`, no committee-signature check, and no other guard. The `require` implements a **first-caller-wins** pattern: whoever calls first wins permanently, and every subsequent call reverts with `"BridgeCommittee: Config already initialized"`.

The existing test confirms the revert is unconditional once the slot is filled: [2](#0-1) 

An attacker monitors the mempool for the deployer's `initialize` transaction, then immediately submits `initializeConfig(malicious_config_address)` with a higher gas price. Because `initialize` and `initializeConfig` are separate transactions, the front-run window is always present.

---

### Impact Explanation

`config` (type `IBridgeConfig`) is the bridge's source of truth for supported tokens, token limits, and chain-ID validation. A malicious implementation can:

- Return manipulated token limits → enable illegitimate mint or unlock amounts.
- Return `false` for all token-support queries → permanently freeze all bridge transfers.
- Return wrong chain IDs → break domain separation and enable cross-chain replay.

Once set, `config` cannot be corrected without redeploying the entire proxy (which requires committee quorum for the upgrade message). The bridge is effectively bricked or under attacker control from the moment of deployment.

This matches the **bridge governance bypass enabling illegitimate mint or unlock** impact class in the allowed-impact gate.

---

### Likelihood Explanation

- Requires zero privilege: any EOA with enough ETH for gas.
- The attack window opens the moment `initialize` is confirmed and closes only when the deployer's `initializeConfig` is mined — typically seconds to minutes.
- Front-running on Ethereum mainnet is trivially achievable via MEV bots or manual gas-price bumping.
- Cost: one transaction, negligible gas.

---

### Recommendation

Add an access-control guard to `initializeConfig`. The simplest fix is to restrict it to the contract's own proxy admin or to the committee itself:

```solidity
// Option A: restrict to a stored admin set during initialize()
function initializeConfig(address _config) external onlyOwner {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}

// Option B: set config atomically inside initialize() so no second transaction is needed
function initialize(
    address[] memory committee,
    uint16[] memory stake,
    uint16 minStakeRequired,
    address _config          // add this parameter
) external initializer {
    ...
    config = IBridgeConfig(_config);
}
```

Option B eliminates the race entirely by collapsing both initialization steps into one atomic transaction.

---

### Proof of Concept

1. Deployer broadcasts `BridgeCommittee.initialize(committee, stake, minStake)` (tx A).
2. Attacker sees tx A in the mempool and broadcasts `BridgeCommittee.initializeConfig(attacker_config)` with higher gas (tx B).
3. Tx B is mined first. `config` is now set to `attacker_config`.
4. Deployer's follow-up `initializeConfig(real_config)` reverts: `"BridgeCommittee: Config already initialized"`.
5. All bridge operations that read `config` (token limits, chain-ID checks) now execute against the attacker-controlled contract.
6. Attacker's `IBridgeConfig` implementation can return arbitrary token limits, enabling illegitimate mint or unlock of bridged assets. [1](#0-0) [2](#0-1)

### Citations

**File:** bridge/evm/contracts/BridgeCommittee.sol (L63-66)
```text
    function initializeConfig(address _config) external {
        require(address(config) == address(0), "BridgeCommittee: Config already initialized");
        config = IBridgeConfig(_config);
    }
```

**File:** bridge/evm/test/BridgeCommitteeTest.t.sol (L55-59)
```text
    function testBridgeCommitteeInitializeConfig() public {
        vm.expectRevert(bytes("BridgeCommittee: Config already initialized"));
        // Initialize the committee with the config contract
        committee.initializeConfig(address(101));
    }
```
