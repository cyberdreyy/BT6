Looking at the `allowContractPushers` function and the `fallback` push path in `CompressedOracle.sol`, I can identify a direct analog to the disabled loan config bug.

**Key observation:** `allowContractPushers` checks `isPusher(creator)` via a live staticcall at registration time. The comment explicitly states this live check replaces the need for a deadline. But the `fallback` push path **never re-checks** `isPusher` — it only reads `namespaceRemapping[msg.sender]`. If the contract pusher's `isPusher` later returns `false`, the stale mapping persists and the revoked pusher can still inject prices. [1](#0-0) [2](#0-1) 

---

### Title
`allowContractPushers` live consent is only checked at registration, not at push time — a revoked contract pusher can inject arbitrary prices into the creator's namespace - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary
`CompressedOracleV1.allowContractPushers` validates consent via a live `isPusher(creator)` staticcall and records `namespaceRemapping[pusher] = creator`. The `fallback` push path reads only `namespaceRemapping[msg.sender]` to select the target namespace and **never re-checks `isPusher`**. If the contract pusher's `isPusher` subsequently returns `false` (e.g., the creator revokes at the pusherContract level), the stale mapping entry remains, and every subsequent fallback call from that pusher still writes into the creator's oracle namespace. A revoked contract pusher can therefore inject arbitrary prices into the creator's feeds, which are consumed by price providers feeding live pool swaps.

### Finding Description

`allowContractPushers` is explicitly designed so that the live `isPusher(creator)` staticcall replaces the EIP-191 signature + deadline required by `allowPushers`. The NatSpec states:

> *"consent is proven by a LIVE `isPusher(creator)` staticcall instead of a signature, so there is nothing to replay and no deadline is needed."* [3](#0-2) 

However, the liveness check is performed **only once**, at registration. The `fallback` push path resolves the target namespace solely from the stored mapping:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

There is no re-invocation of `isPusher`. If the pusherContract's `isPusher` changes to return `false` after registration — for example, because the creator revoked the pusher at the pusherContract level by changing internal state — the oracle is unaware. The stale `namespaceRemapping` entry persists, and every subsequent fallback call from the pusherContract still writes into the creator's namespace.

This is the direct analog of the external report's disabled loan config bug: a status flag (`isPusher`) can be changed to "disabled" but is never re-checked at the point of use (the `fallback` push path).

The `removePushers` function is the correct revocation path on the oracle side, but the design rationale of `allowContractPushers` — "live check, no deadline needed" — creates a false expectation that revoking at the pusherContract level is sufficient. A creator who revokes `isPusher` on their pusherContract but forgets to call `removePushers` on the oracle leaves the delegation fully active. [5](#0-4) 

### Impact Explanation

A revoked contract pusher can push arbitrary prices into the creator's oracle namespace. Price providers (`PriceProvider`, `ProtectedPriceProvider`, `AnchoredPriceProvider`) consume these feeds to compute bid/ask quotes for pool swaps. Injecting a manipulated price causes bad-price execution: traders receive more than the oracle/bin curve permits, or the pool fails to receive owed input, resulting in direct loss of LP assets or protocol fees. This satisfies the "bad-price execution" impact gate.

### Likelihood Explanation

The design rationale explicitly states that the live `isPusher` check provides ongoing authorization, making it natural for creators to revoke at the pusherContract level without calling `removePushers` on the oracle. Any mutable contract pusher — one whose `isPusher` return value can change — creates this window. The trigger requires no privileged access: the pusherContract (or anyone who controls it after a compromise) can call the oracle's `fallback` directly. The existing test suite includes `MockPusherSelective`, a contract whose `isPusher` is conditional on an internal `allowedCreator` field, confirming this scenario is realistic. [6](#0-5) 

### Recommendation

Track which pushers were registered via `allowContractPushers` in a separate mapping (e.g., `mapping(address => bool) public isContractPusher`). In the `fallback`, re-check `isPusher(creator)` for contract pushers before writing to the namespace:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) {
    creator = msg.sender;
} else if (isContractPusher[msg.sender]) {
    // Re-validate live consent on every push
    (bool ok, bytes memory res) = msg.sender.staticcall(
        abi.encodeWithSignature("isPusher(address)", creator)
    );
    if (!ok || !abi.decode(res, (bool))) revert ContractPusherRevoked();
}
```

Set `isContractPusher[pusher] = true` in `allowContractPushers` and clear it in `removePushers` / `revokePusher`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {CompressedOracleV1} from "../contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from "../contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "../contracts/oracles/utils/U64x32.sol";

/// @dev A mutable contract pusher whose isPusher can be toggled off
contract MutablePusher {
    address public allowedCreator;

    constructor(address _creator) { allowedCreator = _creator; }

    function isPusher(address caller) external view returns (bool) {
        return caller == allowedCreator;
    }

    /// Creator revokes at the pusherContract level
    function revokeCreator() external { allowedCreator = address(0); }

    /// Anyone who controls this contract can still push after revocation
    function push(address oracle, bytes memory payload) external {
        (bool ok,) = oracle.call(payload);
        require(ok, "push failed");
    }
}

contract RevokedContractPusherTest is Test {
    CompressedOracleV1 private oracle;
    address private creator = address(0xC0FFEE);
    MutablePusher private pusherContract;

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        vm.warp(1_700_000_000);
        pusherContract = new MutablePusher(creator);
    }

    function _wordAt(uint8 slotId, uint8 pos, uint48 raw, uint56 tsMs)
        internal pure returns (bytes memory)
    {
        uint256 word = (uint256(tsMs) << 8) | uint256(slotId);
        word |= uint256(raw) << (208 - uint256(pos) * 48);
        return abi.encodePacked(word);
    }

    function test_RevokedContractPusherCanStillInjectPrices() public {
        // 1. Creator registers the contract pusher (isPusher returns true)
        address[] memory pushers = new address[](1);
        pushers[0] = address(pusherContract);
        vm.prank(creator);
        oracle.allowContractPushers(pushers);
        assertEq(oracle.namespaceRemapping(address(pusherContract)), creator);

        // 2. Creator revokes at the pusherContract level — isPusher now returns false
        pusherContract.revokeCreator();
        assertFalse(pusherContract.isPusher(creator));

        // 3. Oracle still holds the stale delegation
        assertEq(oracle.namespaceRemapping(address(pusherContract)), creator);

        // 4. Attacker pushes a manipulated price via the rev

### Citations

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L245-260)
```text
    function removePushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];
            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            if (namespaceRemapping[pusher] == msg.sender) {
                namespaceRemapping[pusher] = address(0);
                emit PusherRevoked(pusher, msg.sender);
            } else {
                revert InvalidManager(msg.sender);
            }
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-321)
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
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracleContractPushers.t.sol (L24-35)
```text
/// @dev Mock that returns true only for a specific creator
contract MockPusherSelective {
    address public allowedCreator;

    constructor(address _allowedCreator) {
        allowedCreator = _allowedCreator;
    }

    function isPusher(address caller) external view returns (bool) {
        return caller == allowedCreator;
    }
}
```
