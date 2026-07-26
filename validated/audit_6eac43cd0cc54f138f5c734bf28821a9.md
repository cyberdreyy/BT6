### Title
Unprotected `initializeConfig` in `BridgeCommittee` Allows Any Caller to Inject a Malicious Config, Permanently Locking Bridge Governance — (File: bridge/evm/contracts/BridgeCommittee.sol)

---

### Summary

`BridgeCommittee.initializeConfig` is an `external` function with no access control. Any address can call it before the legitimate deployer does. Because `config` is the sole source of the chain-ID used to authenticate every non-TOKEN\_TRANSFER governance message (emergency pause, blocklist, upgrade, limit/price updates), an attacker who front-runs this call with a malicious `IBridgeConfig` contract permanently disables all bridge governance, leaving locked funds with no recovery path.

---

### Finding Description

`BridgeCommittee.sol` lines 63-66:

```solidity
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
``` [1](#0-0) 

There is no `onlyOwner`, no committee-signature gate, and no deployer check. The sole guard is the one-time `address(config) == address(0)` condition. Whoever calls this first wins.

The `config` object is consumed in `MessageVerifier.verifyMessageAndSignatures` for every message type except `TOKEN_TRANSFER`:

```solidity
if (messageType != BridgeUtils.TOKEN_TRANSFER) {
    require(
        message.chainID == committee.config().chainID(), "MessageVerifier: Invalid chain ID"
    );
    require(message.nonce == nonces[message.messageType], "MessageVerifier: Invalid nonce");
    nonces[message.messageType]++;
}
``` [2](#0-1) 

This covers `EMERGENCY_OP` (pause/unpause), `BLOCKLIST`, `UPDATE_BRIDGE_LIMIT`, `UPDATE_TOKEN_PRICE`, and `UPGRADE`. If `committee.config().chainID()` returns any value other than the real chain ID, every one of these calls reverts with `"MessageVerifier: Invalid chain ID"`.

The deployment script sends these as separate on-chain transactions, creating an exploitable window:

```solidity
// initialize config in the bridge committee
BridgeCommittee(bridgeCommittee).initializeConfig(address(bridgeConfig));
``` [3](#0-2) 

An attacker who observes the `BridgeCommittee` proxy deployment in the mempool can submit a higher-gas call to `initializeConfig` with a malicious `IBridgeConfig` address before the deployment script's call lands. The legitimate call then reverts with `"Config already initialized"`, and the malicious config is permanently locked in.

---

### Impact Explanation

With a malicious config in place:

- **Emergency pause is disabled.** `executeEmergencyOpWithSignatures` reverts on the chain-ID check, so the bridge cannot be frozen even if an active exploit is draining the vault.
- **Upgrade is disabled.** `upgradeWithSignatures` reverts, so the implementation cannot be patched.
- **Blocklist, limit, and price updates are disabled.** All committee governance is dead.
- **Token transfers still execute** (the `TOKEN_TRANSFER` branch skips the chain-ID check), so the vault continues to accept and release funds under the broken governance regime.

The net result is a bridge that processes transfers but can never be paused, upgraded, or administratively corrected. Any subsequent exploit of the bridge logic has no circuit-breaker. Funds locked in `BridgeVault` are permanently at risk with no recovery path. This matches the bounty's **permanent fund lock / harmful smart-contract behavior** impact class.

---

### Likelihood Explanation

Ethereum mainnet front-running is well-understood and routinely executed by MEV bots. The deployment script broadcasts multiple sequential transactions; the gap between the `BridgeCommittee` proxy deployment and the `initializeConfig` call is observable in the public mempool. A single higher-gas transaction is sufficient to win the race. No privileged access, leaked key, or validator collusion is required — only an ordinary EOA.

---

### Recommendation

1. **Combine initialization atomically.** Move the config assignment into the `initialize` function (which is already protected by OpenZeppelin's `initializer` modifier) so there is no two-step window.
2. **Or add an owner/deployer guard.** Store `msg.sender` during `initialize` and require it in `initializeConfig`.
3. **Or require committee signatures** on the config-setting call, consistent with how all other governance actions are authorized.

---

### Proof of Concept

1. Attacker watches the Ethereum mempool for the `BridgeCommittee` UUPS proxy deployment transaction.
2. Attacker deploys `MaliciousConfig` implementing `IBridgeConfig` with `chainID()` returning `0`.
3. Attacker submits `BridgeCommittee(proxy).initializeConfig(address(maliciousConfig))` with higher gas, front-running the deployment script.
4. Deployment script's `initializeConfig` call reverts: `"BridgeCommittee: Config already initialized"`.
5. From this point, every call to `executeEmergencyOpWithSignatures`, `upgradeWithSignatures`, `updateBridgeLimitWithSignatures`, `updateTokenPriceWithSignatures`, and `updateBlocklistWithSignatures` reverts with `"MessageVerifier: Invalid chain ID"`.
6. The bridge continues processing `TOKEN_TRANSFER` messages (vault funds flow), but the bridge can never be paused or upgraded. Any subsequent vulnerability in the bridge logic has no governance remedy, resulting in permanent fund lock.

### Citations

**File:** bridge/evm/contracts/BridgeCommittee.sol (L63-66)
```text
    function initializeConfig(address _config) external {
        require(address(config) == address(0), "BridgeCommittee: Config already initialized");
        config = IBridgeConfig(_config);
    }
```

**File:** bridge/evm/contracts/utils/MessageVerifier.sol (L43-50)
```text
        if (messageType != BridgeUtils.TOKEN_TRANSFER) {
            // verify chain ID
            require(
                message.chainID == committee.config().chainID(), "MessageVerifier: Invalid chain ID"
            );
            require(message.nonce == nonces[message.messageType], "MessageVerifier: Invalid nonce");
            nonces[message.messageType]++;
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
