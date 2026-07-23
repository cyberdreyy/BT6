### Title
`revokePusher()` Self-Revocation Is Permanently Bypassable via `allowContractPushers()` Re-Invocation, Allowing Bad Prices to Persist in Creator Namespace Feeds — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`revokePusher()` is designed to let a contract pusher unilaterally detach from a creator's namespace. However, because `allowContractPushers()` performs only a **live** `isPusher(creator)` staticcall and writes `namespaceRemapping[pusher] = msg.sender` unconditionally, the creator can immediately re-establish the delegation after revocation with no additional consent from the pusher. The pusher's self-revocation is silently nullified, and any bad prices it was pushing continue to land in the creator's namespace and reach live pool swaps.

---

### Finding Description

The `allowPushers` NatSpec explicitly acknowledges the replay risk:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [1](#0-0) 

For EOA pushers the deadline provides a bounded window after which replay is impossible. For **contract pushers** there is no deadline at all — the comment says "there is nothing to replay" — but this reasoning is wrong for the revocation case: [2](#0-1) 

`allowContractPushers` simply calls `pusher.isPusher(msg.sender)` and, if it returns `true`, overwrites `namespaceRemapping[pusher]` with `msg.sender`. It does **not** check whether the pusher has previously called `revokePusher()`.

`revokePusher()` only clears the oracle-side mapping: [3](#0-2) 

It does not — and cannot — alter the pusher contract's own `isPusher()` return value. Because `allowContractPushers` re-reads that live value, the creator can call it again at any time and the mapping is restored.

**Step-by-step attack path:**

1. Creator calls `allowContractPushers([pusherContract])` → `namespaceRemapping[pusherContract] = creator`.
2. `pusherContract` detects a compromise (e.g., its price source is manipulated) and calls `revokePusher()` → `namespaceRemapping[pusherContract] = address(0)`.
3. Creator (unaware of the compromise, or maliciously) calls `allowContractPushers([pusherContract])` again.
4. `pusherContract.isPusher(creator)` still returns `true` (the oracle-side revocation has no effect on the pusher contract's internal state).
5. `namespaceRemapping[pusherContract] = creator` is written again — revocation nullified.
6. `pusherContract` continues pushing manipulated prices into the creator's namespace via the `fallback` path: [4](#0-3) 

7. Pools reading `feedIdOf(creator, slotIndex, positionIndex)` receive the bad mid/spread values and execute swaps at those prices.

The same bypass applies to the EOA path (`allowPushers`) while the original deadline has not yet expired: the creator can replay the identical `(deadline, pusher, signature)` tuple immediately after `revokePusher()` clears the mapping, because no nonce or revocation flag is recorded. [5](#0-4) 

---

### Impact Explanation

The `fallback` push path writes the manipulated slot word directly into the creator's storage namespace. `getOracleData` decodes it and returns the corrupted `mid`, `spread0`, `spread1` to any price provider that reads the feed. Pools executing swaps against that provider receive a bad bid/ask quote, causing traders to receive more than the oracle curve permits or the pool to receive less than owed — a direct swap conservation failure and potential pool insolvency.

---

### Likelihood Explanation

- **Contract-pusher path**: The creator needs only to call one public function (`allowContractPushers`) with no additional material (no signature, no deadline). As long as the pusher contract's `isPusher()` has not been updated to return `false`, the bypass succeeds unconditionally. This is a realistic operational scenario: a pusher contract may be immutable, or the creator may act before the pusher contract owner can update it.
- **EOA-pusher path**: The creator must hold the original signature and the deadline must not yet have expired. Delegations are typically set with multi-day deadlines (tests use `block.timestamp + 1 days`), so the window is wide. [6](#0-5) 

---

### Recommendation

1. **Record revocations on-chain.** Add a `mapping(address => bool) public revokedPushers` (or a per-pusher nonce). `revokePusher()` sets the flag; `allowContractPushers` and `allowPushers` must revert if the flag is set.
2. **Alternatively**, require the pusher to explicitly clear its own revocation flag before a new delegation can be accepted, making re-delegation an opt-in act by the pusher rather than the creator.
3. For the EOA path, include a per-pusher nonce in the signed message so that `revokePusher()` can increment it, invalidating all previously signed consents regardless of their deadline.

---

### Proof of Concept

```solidity
// Uses MockPusherAllowed from the existing test suite (isPusher always returns true)
function testRevokePusherBypassViaAllowContractPushers() public {
    MockPusherAllowed pusherContract = new MockPusherAllowed();

    // 1. Creator establishes delegation
    vm.prank(creator);
    oracle.allowContractPushers(_pushers(address(pusherContract)));
    assertEq(oracle.namespaceRemapping(address(pusherContract)), creator);

    // 2. Pusher self-revokes (e.g., detects compromise)
    vm.prank(address(pusherContract));
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(address(pusherContract)), address(0));

    // 3. Creator immediately re-establishes delegation — no new consent needed
    vm.prank(creator);
    oracle.allowContractPushers(_pushers(address(pusherContract)));
    // isPusher(creator) still returns true → bypass succeeds
    assertEq(oracle.namespaceRemapping(address(pusherContract)), creator);

    // 4. Pusher pushes manipulated price into creator's namespace
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 badRaw = _packRaw(9_999_999, 0, 0); // extreme price
    vm.prank(address(pusherContract));
    (bool ok,) = address(oracle).call(_wordAt(0, 0, badRaw, tsMs));
    assertTrue(ok);

    // 5. Creator's feed now contains the bad price
    IOffchainOracle.OracleData memory data =
        oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
    assertEq(data.price, U64x32.decode(uint32(badRaw >> 16)));
    // Pools reading this feed will execute swaps at the manipulated price
}
``` [7](#0-6) [8](#0-7) [3](#0-2)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
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

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracleContractPushers.t.sol (L201-211)
```text
    function testContractPusherCanSelfRevoke() public {
        MockPusherAllowed pusherContract = new MockPusherAllowed();

        vm.prank(creator);
        oracle.allowContractPushers(_pushers(address(pusherContract)));
        assertEq(oracle.namespaceRemapping(address(pusherContract)), creator);

        vm.prank(address(pusherContract));
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(address(pusherContract)), address(0));
    }
```
