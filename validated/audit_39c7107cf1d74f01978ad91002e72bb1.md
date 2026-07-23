### Title
Permissionless `allowContractPushers` Overwrites Existing Delegation, Enabling Namespace Hijack and Feed Staleness DoS - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary

`CompressedOracleV1::allowContractPushers` trusts a live `isPusher(caller)` staticcall on the pusher contract as the sole consent proof, but performs no check on whether an existing `namespaceRemapping` entry is already set. Any unprivileged caller can overwrite a legitimate creator's delegation by supplying a permissive pusher contract address, redirecting that pusher's slot writes from the creator's namespace to the attacker's namespace and leaving the creator's feeds permanently stale.

### Finding Description

`allowContractPushers` is a fully public function. Its only guard is:

```solidity
(bool ok, bytes memory res) = pusher.staticcall(
    abi.encodeWithSignature("isPusher(address)", msg.sender)
);
require(ok);
bool allowed = abi.decode(res, (bool));
require(allowed);

namespaceRemapping[pusher] = msg.sender;   // unconditional overwrite
``` [1](#0-0) 

The function checks that the pusher contract *currently* returns `true` for `msg.sender`, but it does **not** check:

1. Whether `namespaceRemapping[pusher]` is already set to a different creator.
2. Whether `msg.sender` is the creator that the pusher contract was originally designed to serve.

A contract pusher that implements `isPusher` as `return true` for any caller (a realistic design for a public keeper or bot) can therefore be hijacked by any EOA. The attacker calls `allowContractPushers([P])`, the oracle calls `P.isPusher(attacker)` → `true`, and `namespaceRemapping[P]` is overwritten from the legitimate creator `C` to the attacker `A`. [2](#0-1) 

After the overwrite, every subsequent fallback push from `P` resolves the namespace as:

```solidity
address creator = namespaceRemapping[msg.sender]; // now = attacker A
if (creator == address(0)) creator = msg.sender;
``` [3](#0-2) 

Slot writes land in `A`'s namespace (`feedIdOf(A, slot, pos)`) instead of `feedIdOf(C, slot, pos)`. Creator `C`'s feeds receive no further updates.

### Impact Explanation

Any pool whose `AnchoredPriceProvider` (or `ProtectedPriceProvider`) is backed by `CompressedOracleV1` and reads `feedIdOf(C, slot, pos)` will observe a monotonically aging `refTime`. Once `block.timestamp − refTime > MAX_REF_STALENESS`, the provider returns `(0, type(uint128).max)` and `getBidAndAskPrice` reverts with `FeedStalled`. [4](#0-3) 

The pool's `swap` call propagates the revert as `PriceProviderFailed`, making the pool's swap path completely unusable — matching the "broken core pool functionality / unusable swap flows" impact class. Users holding positions in the pool cannot trade out of them for as long as the delegation remains hijacked.

### Likelihood Explanation

The trigger is fully unprivileged: any EOA can call `allowContractPushers`. The only precondition is that the targeted pusher contract returns `true` from `isPusher` for the attacker's address. Public keeper contracts, shared oracle bots, or any pusher that does not restrict `isPusher` to a single creator address satisfy this condition. The production deployment script already uses `allowContractPushers` with a live contract pusher, confirming the path is active. [5](#0-4) 

### Recommendation

Add a guard that prevents overwriting an existing delegation without the current creator's consent:

```solidity
function allowContractPushers(address[] calldata pushers) external {
    for (uint256 i; i < pushers.length; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) revert NoSelfRemapping();

        // Prevent hijacking an existing delegation
        address existing = namespaceRemapping[pusher];
        if (existing != address(0) && existing != msg.sender) revert AlreadyDelegated(pusher, existing);

        (bool ok, bytes memory res) = pusher.staticcall(
            abi.encodeWithSignature("isPusher(address)", msg.sender)
        );
        require(ok);
        require(abi.decode(res, (bool)));

        namespaceRemapping[pusher] = msg.sender;
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

Alternatively, require the pusher contract to implement `isPusher` with strict creator-binding (enforced by documentation and audited reference implementations), and add an on-chain check that the existing mapping is zero before writing.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {CompressedOracleV1} from ".../CompressedOracle.sol";

// Permissive pusher: returns true for any caller
contract PermissivePusher {
    function isPusher(address) external pure returns (bool) { return true; }
}

contract HijackTest {
    function run(CompressedOracleV1 oracle) external {
        address creator  = address(0xC0FFEE);
        address attacker = address(0xA77AC);

        PermissivePusher pusher = new PermissivePusher();

        // Step 1: legitimate creator establishes delegation
        vm.prank(creator);
        oracle.allowContractPushers(_arr(address(pusher)));
        assert(oracle.namespaceRemapping(address(pusher)) == creator);

        // Step 2: attacker overwrites delegation — no signature, no creator approval
        vm.prank(attacker);
        oracle.allowContractPushers(_arr(address(pusher)));
        assert(oracle.namespaceRemapping(address(pusher)) == attacker); // hijacked

        // Step 3: pusher's next fallback push lands in attacker's namespace, not creator's
        // creator's feedIdOf(creator, slot, pos) receives no further updates → stale → FeedStalled
    }

    function _arr(address a) internal pure returns (address[] memory r) {
        r = new address[](1); r[0] = a;
    }
}
``` [1](#0-0) [6](#0-5)

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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
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
