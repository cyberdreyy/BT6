### Title
`allowContractPushers` Silently Overwrites Existing Delegation, Leaving Original Creator Unable to Revoke — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`CompressedOracleV1.allowContractPushers` writes `namespaceRemapping[pusher] = msg.sender` without first checking whether the pusher is already delegated to a different creator. Any creator whose `isPusher(newCreator)` check passes can silently overwrite an existing delegation. The original creator's entry in `namespaceRemapping` is replaced without cleanup, and because `removePushers` guards on `namespaceRemapping[pusher] == msg.sender`, the original creator can no longer revoke the pusher. Their feeds stop receiving updates, become stale, and every pool swap that reads those feeds reverts with `FeedStalled`.

### Finding Description

`allowContractPushers` proves consent via a live `isPusher(creator)` staticcall and then unconditionally writes the new mapping:

```solidity
// CompressedOracle.sol lines 217-234
function allowContractPushers(address[] calldata pushers) external {
    ...
    (bool ok, bytes memory res) = pusher.staticcall(
        abi.encodeWithSignature("isPusher(address)", msg.sender)
    );
    require(ok);
    bool allowed = abi.decode(res, (bool));
    require(allowed);

    namespaceRemapping[pusher] = msg.sender;   // ← overwrites any existing mapping
    emit PusherAuthorized(pusher, msg.sender);
}
``` [1](#0-0) 

There is no guard of the form `require(namespaceRemapping[pusher] == address(0))`. After the overwrite, `removePushers` enforces:

```solidity
// lines 253-258
if (namespaceRemapping[pusher] == msg.sender) {
    namespaceRemapping[pusher] = address(0);
    ...
} else {
    revert InvalidManager(msg.sender);   // ← original creator hits this
}
``` [2](#0-1) 

The original creator is permanently locked out of revoking the pusher. This is the direct analog to the `last_tvl` bug: just as removing a price pair leaves a stale `last_tvl` entry that continues to influence aggregation, overwriting a delegation leaves the original creator's management state permanently corrupted — they can neither revoke the pusher nor restore their feed update flow.

The `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`, so after the overwrite all of the pusher's writes land in the attacker's namespace, not the victim's:

```solidity
// lines 315-316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [3](#0-2) 

### Impact Explanation

Once the victim creator's pusher is stolen:
1. No new data arrives in the victim's namespace.
2. The slot timestamp ages past `MAX_TIME_DELTA`.
3. Every price provider backed by that feed returns `refTime` that fails the staleness check, causing `getBidAndAskPrice` to revert with `FeedStalled`.
4. Every pool swap that calls that provider reverts — the pool is completely unusable for swaps until the creator manually sets up a new pusher.

This matches the allowed impact gate: **broken core pool functionality causing unusable swap flows**.

### Likelihood Explanation

The condition is that the pusher contract's `isPusher(attacker)` returns `true`. This is realistic when:
- The pusher is a shared relay service that authorises multiple creators.
- The pusher contract has a permissive or buggy `isPusher` implementation (the test suite ships `MockPusherAllowed` which returns `true` for anyone).
- The attacker deploys a contract that wraps the victim's pusher and returns `true` for themselves.

The trigger is fully unprivileged: any address can be a creator in the registrationless system, and `allowContractPushers` is a public function.

### Recommendation

Add an existence check before overwriting the mapping:

```solidity
function allowContractPushers(address[] calldata pushers) external {
    ...
    address existing = namespaceRemapping[pusher];
    require(existing == address(0) || existing == msg.sender, AlreadyDelegated(pusher));
    namespaceRemapping[pusher] = msg.sender;
    ...
}
```

This ensures a pusher can only be re-delegated by its current manager, preserving the original creator's ability to revoke.

### Proof of Concept

```solidity
// Attacker deploys a contract that returns isPusher = true for anyone
contract GreedyPusher {
    function isPusher(address) external pure returns (bool) { return true; }
}

// 1. Victim creator A delegates GreedyPusher
vm.prank(creatorA);
oracle.allowContractPushers([address(greedy)]);
assertEq(oracle.namespaceRemapping(address(greedy)), creatorA);

// 2. Attacker B (any address) overwrites the delegation
vm.prank(attackerB);
oracle.allowContractPushers([address(greedy)]);
assertEq(oracle.namespaceRemapping(address(greedy)), attackerB); // stolen

// 3. Creator A can no longer revoke
address[] memory p = new address[](1); p[0] = address(greedy);
vm.prank(creatorA);
vm.expectRevert(ICompressedOracleV1.InvalidManager.selector);
oracle.removePushers(p); // reverts — creatorA is no longer the manager

// 4. GreedyPusher's future pushes land in attackerB's namespace; creatorA's feeds go stale
// → price provider backed by creatorA's feed reverts FeedStalled on next pool swap
``` [1](#0-0) [2](#0-1)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L217-234)
```text
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
