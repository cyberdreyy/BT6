### Title
`allowContractPushers` Overwrites Existing Delegation Without Original Creator's Consent, Enabling Namespace Hijack That Stales Pool Feeds — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowContractPushers` does not verify whether a pusher contract is already mapped to another creator's namespace. Any caller for whom the pusher contract returns `isPusher(caller) == true` can silently overwrite an existing `namespaceRemapping` entry, redirecting the pusher's slot writes away from the legitimate creator's namespace. The legitimate creator's feeds then receive no further updates, become stale, and every `AnchoredPriceProvider` anchored to those feeds halts with `FeedStalled`, making all dependent pool swaps unusable.

---

### Finding Description

`allowContractPushers` proves consent via a live `isPusher(msg.sender)` staticcall and then unconditionally writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
// CompressedOracle.sol L217-233
function allowContractPushers(address[] calldata pushers) external {
    uint256 l = pushers.length;
    for (uint256 i; i < l; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) { revert NoSelfRemapping(); }

        (bool ok, bytes memory res) = pusher.staticcall(
            abi.encodeWithSignature("isPusher(address)", msg.sender)
        );
        require(ok);
        bool allowed = abi.decode(res, (bool));
        require(allowed);

        namespaceRemapping[pusher] = msg.sender;   // ← no check on existing value
        emit PusherAuthorized(pusher, msg.sender);
    }
}
``` [1](#0-0) 

There is no guard of the form `require(namespaceRemapping[pusher] == address(0) || namespaceRemapping[pusher] == msg.sender)`. Any caller who satisfies the pusher contract's `isPusher` check can overwrite a delegation that was already established by a different creator.

Contrast this with the EOA path `allowPushers`, where the pusher's EIP-191 signature explicitly encodes `msg.sender` (the intended creator):

```solidity
// CompressedOracle.sol L204-207
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
``` [2](#0-1) 

The EOA path binds consent to one specific creator; the contract-pusher path does not. This asymmetry is the root cause.

The `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`, falling back to `msg.sender` itself:

```solidity
// CompressedOracle.sol L315-316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [3](#0-2) 

After a hijack, every slot word the pusher contract sends is stored under the attacker's namespace key (`attacker_address << 96 | slotId`), not the legitimate creator's key. The legitimate creator's storage slots receive no further writes.

`AnchoredPriceProvider._readLeg` then fails the staleness check:

```solidity
// AnchoredPriceProvider.sol L282-283
if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
``` [4](#0-3) 

and `getBidAndAskPrice` reverts with `FeedStalled`:

```solidity
// AnchoredPriceProvider.sol L216
if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
``` [5](#0-4) 

Every pool swap that calls this provider is blocked.

---

### Impact Explanation

Once the delegation is hijacked the legitimate creator's feeds receive no further updates. After `MAX_REF_STALENESS` seconds every `AnchoredPriceProvider` anchored to those feeds returns `FeedStalled`, making pool swaps revert. Liquidity providers cannot exit via swaps; traders cannot execute. This satisfies the impact gate criterion **"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."**

---

### Likelihood Explanation

The exploit requires a pusher contract whose `isPusher(address)` returns `true` for the attacker. This is a realistic production scenario: a shared price-aggregator contract that is designed to serve multiple creators (returning `true` for any registered participant) is a natural deployment pattern. The `MockPusherAllowed` contract in the test suite (`isPusher` always returns `true`) demonstrates the pattern exists and is tested. An attacker needs only one such permissive pusher contract that a legitimate creator has already delegated.

---

### Recommendation

Before writing `namespaceRemapping[pusher] = msg.sender`, verify that the slot is either unoccupied or already owned by the caller:

```solidity
address existing = namespaceRemapping[pusher];
if (existing != address(0) && existing != msg.sender) {
    revert AlreadyDelegated(pusher, existing);
}
```

Alternatively, mirror the EOA path: require the pusher contract to sign a message that encodes the specific creator address, binding consent to exactly one namespace.

---

### Proof of Concept

```
Setup:
  oracle = new CompressedOracleV1(owner, 0);
  pusherContract = new MockPusherAllowed();   // isPusher(any) == true

Step 1 – legitimate delegation:
  vm.prank(creatorA);
  oracle.allowContractPushers([pusherContract]);
  // namespaceRemapping[pusherContract] == creatorA  ✓

Step 2 – attacker hijacks:
  vm.prank(attacker);
  oracle.allowContractPushers([pusherContract]);
  // namespaceRemapping[pusherContract] == attacker  ← overwritten, no revert

Step 3 – pusher writes land in attacker's namespace:
  vm.prank(address(pusherContract));
  oracle.call(slotWord);
  // data stored at key = (attacker << 96 | slotId)
  // creatorA's slot unchanged → timestampMs stays at old value

Step 4 – creatorA's feed is stale:
  oracle.getOracleData(feedIdOf(creatorA, slotId, pos)).timestampMs == old_ts

Step 5 – pool swap reverts:
  pool.swap(...)
  → provider.getBidAndAskPrice()
  → _readLeg: _isStale == true → returns (mid, spread, refTime, false)
  → revert FeedStalled()
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L217-233)
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
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L216-216)
```text
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L282-283)
```text
        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
