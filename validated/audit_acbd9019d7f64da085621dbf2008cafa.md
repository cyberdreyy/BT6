Looking at the codebase, I need to trace the analog to the Llama bug: a global state variable that determines *how* an already-committed operation executes can be changed by a separate, independently-authorized action.

In Llama: `authorizedScripts[target]` determines `call` vs `delegate_call` for an already-approved action, and can be flipped by a different set of signers.

In Metric OMM: `namespaceRemapping[pusher]` determines *which creator's namespace* a contract pusher writes into, and can be overwritten by a different creator calling `allowContractPushers`.

Let me verify the exact code path: [1](#0-0) [2](#0-1) 

The `allowContractPushers` function has no guard against overwriting an existing delegation. Any caller for whom `pusherContract.isPusher(caller)` returns `true` can silently redirect the pusher to their own namespace, even if the pusher is already delegated to a different creator. Future pushes then land in the new creator's namespace, leaving the original creator's feed frozen at its last value — a stale price that price providers and pools will continue to consume.

---

### Title
Contract Pusher Namespace Can Be Silently Redirected by a Different Creator After Delegation, Delivering Stale Prices to Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`allowContractPushers` contains no guard against overwriting an existing `namespaceRemapping` entry. Any address for which the pusher contract's `isPusher(caller)` returns `true` can atomically redirect a live contract pusher from creator A's namespace to their own, freezing A's feed at its last pushed value while pools continue to read it as current.

### Finding Description
`allowContractPushers` proves consent via a live `isPusher(msg.sender)` staticcall and then unconditionally writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
// CompressedOracle.sol – allowContractPushers (lines 217-233)
function allowContractPushers(address[] calldata pushers) external {
    uint256 l = pushers.length;
    for (uint256 i; i < l; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) { revert NoSelfRemapping(); }

        (bool ok, bytes memory res) =
            pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
        require(ok);
        bool allowed = abi.decode(res, (bool));
        require(allowed);

        namespaceRemapping[pusher] = msg.sender;   // ← no check for existing entry
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

There is no check of the form `require(namespaceRemapping[pusher] == address(0) || namespaceRemapping[pusher] == msg.sender)`. A pusher contract that legitimately authorizes more than one creator (e.g., a shared oracle relay that serves multiple feed owners) allows any of those creators to call `allowContractPushers` and overwrite the current mapping.

The `fallback` push path resolves the namespace at push time:

```solidity
// CompressedOracle.sol – fallback (lines 315-316)
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
```

After the redirect, every subsequent push from `pusherContract` lands in creator B's namespace. Creator A's feed — identified by `feedIdOf(A, slotIndex, positionIndex)` — receives no further updates and its stored price becomes permanently stale. [1](#0-0) [3](#0-2) 

### Impact Explanation
Any price provider or `AnchoredPriceProvider` configured to read `feedIdOf(A, slotIndex, positionIndex)` will return the frozen stale price for every subsequent pool swap. Depending on market movement, this stale bid/ask can be arbitrarily far from the true market price, allowing traders to extract value from LPs or causing swaps to execute at incorrect rates — a direct loss of LP principal and protocol fees. This matches the allowed impact: *"Bad-price execution: stale bid/ask quote reaches a pool swap."*

### Likelihood Explanation
The trigger is semi-trusted and requires no privileged role:
- The pusher contract must expose `isPusher(address)` returning `true` for more than one address (a realistic design for shared oracle relay infrastructure).
- Creator B needs only to call `allowContractPushers([pusherContract])` — a public, permissionless function — while `pusherContract.isPusher(B)` returns `true`.
- Creator A has no on-chain mechanism to lock or protect their existing delegation.
- The attack is silent: no event distinguishes a first-time delegation from an overwrite, and the price provider continues returning values without error.

### Recommendation
Add an existence check before writing the new mapping:

```solidity
function allowContractPushers(address[] calldata pushers) external {
    uint256 l = pushers.length;
    for (uint256 i; i < l; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) revert NoSelfRemapping();

        // Prevent silent overwrite of an existing delegation
        address existing = namespaceRemapping[pusher];
        if (existing != address(0) && existing != msg.sender) {
            revert AlreadyDelegated(pusher, existing);
        }

        (bool ok, bytes memory res) =
            pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
        require(ok);
        bool allowed = abi.decode(res, (bool));
        require(allowed);

        namespaceRemapping[pusher] = msg.sender;
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

Alternatively, require the current creator to explicitly call `removePushers` before a new creator can claim the same pusher, mirroring the pattern used in `removePushers` which already enforces `namespaceRemapping[pusher] == msg.sender`.

### Proof of Concept

```
Setup:
  pusherContract.isPusher(creatorA) → true
  pusherContract.isPusher(creatorB) → true   // shared relay, both authorized

Step 1 – creatorA establishes delegation:
  vm.prank(creatorA);
  oracle.allowContractPushers([pusherContract]);
  // namespaceRemapping[pusherContract] == creatorA ✓

Step 2 – pusherContract pushes price $1000 into creatorA's namespace:
  vm.prank(address(pusherContract));
  oracle.call(wordAt(slotId=0, pos=0, price=1_000_000, ts=T0));
  // feedIdOf(creatorA, 0, 0).price == $1000 ✓

Step 3 – Price provider for creatorA's pool is reading feedIdOf(creatorA, 0, 0).

Step 4 – creatorB silently redirects the pusher (no creatorA consent needed):
  vm.prank(creatorB);
  oracle.allowContractPushers([pusherContract]);
  // namespaceRemapping[pusherContract] == creatorB  ← OVERWRITTEN

Step 5 – Market moves to $1100; pusherContract pushes $1100:
  vm.prank(address(pusherContract));
  oracle.call(wordAt(slotId=0, pos=0, price=1_100_000, ts=T1));
  // feedIdOf(creatorB, 0, 0).price == $1100
  // feedIdOf(creatorA, 0, 0).price == $1000  ← FROZEN / STALE

Step 6 – Pool swap reads creatorA's feed → executes at stale $1000.
  Trader arbitrages the $100 gap; LPs bear the loss.
``` [1](#0-0) [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L171-178)
```text
    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
    }
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
