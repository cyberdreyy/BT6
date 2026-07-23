### Title
`allowPushers` Delegation Signature Is Never Consumed, Allowing Creator to Silently Re-Establish Delegation After Pusher Revokes — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies the pusher's EIP-191 consent signature but never marks it as used. A creator who holds a valid (not-yet-expired) signature can replay it an unlimited number of times. Because `revokePusher` only clears `namespaceRemapping[pusher]` without invalidating the original signature, the creator can immediately re-establish delegation in the same block the pusher revokes, making `revokePusher` a no-op for the entire lifetime of the deadline.

---

### Finding Description

`allowPushers` builds and verifies a consent hash but stores no record that the signature was consumed:

```solidity
// CompressedOracle.sol lines 192-211
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));

    namespaceRemapping[pusher] = msg.sender;   // ← state written
    emit PusherAuthorized(pusher, msg.sender);
    // ← signature NEVER invalidated / marked used
}
``` [1](#0-0) 

`revokePusher` clears only the mapping:

```solidity
// lines 238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);   // ← only state cleared
    emit PusherRevoked(msg.sender, creator);
    // ← original signature still valid and replayable
}
``` [2](#0-1) 

There is no nonce, no used-hash mapping, and no other consumed-signature tracking anywhere in the contract: [3](#0-2) 

The documentation acknowledges the risk but misidentifies the deadline as the fix: *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it"* — the deadline prevents use after expiry but does **not** prevent unlimited replays within the deadline window. [4](#0-3) 

---

### Impact Explanation

After a pusher calls `revokePusher()`, the creator can call `allowPushers` again in the same block with the identical signature, restoring `namespaceRemapping[pusher] = creator`. The pusher's revocation is completely nullified for the entire remaining lifetime of the deadline (up to whatever value the creator chose when the signature was originally requested).

Concrete consequences:
- Every push the pusher makes continues to land in the **creator's namespace** rather than the pusher's own namespace, against the pusher's explicit intent.
- If the pusher operates their own pool or feed that reads from their own namespace, those reads receive zero/stale data while their pushes are silently redirected to the creator.
- A creator who controls a pool can keep a compromised or colluding pusher's data flowing into their namespace feeds, driving live swap pricing with data the pusher intended to stop providing.
- The `revokePusher` function is broken as a security primitive for the full deadline window.

---

### Likelihood Explanation

The trigger requires only that the creator holds a not-yet-expired signature — a normal precondition since the creator must have called `allowPushers` at least once. No privileged access, no special setup, and no cost beyond a single transaction. Any creator who wants to retain a pusher against their will can do so trivially.

---

### Recommendation

Record each consumed signature hash in a `mapping(bytes32 => bool) private _usedDelegationHashes` and revert if the hash has already been used:

```solidity
mapping(bytes32 => bool) private _usedDelegationHashes;

function allowPushers(...) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(!_usedDelegationHashes[hash], SignatureAlreadyUsed());
    require(pusher == ECDSA.recover(hash, signatures[i]));
    _usedDelegationHashes[hash] = true;

    namespaceRemapping[pusher] = msg.sender;
    emit PusherAuthorized(pusher, msg.sender);
}
```

This ensures each consent signature can establish delegation exactly once, so `revokePusher` is permanently effective.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = block.timestamp + 1 day
bytes memory sig = sign(PUSHER_KEY, abi.encode(chainid, oracle, deadline, pusher, creator));

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — deadline still valid
vm.prank(creator);
oracle.allowPushers(deadline, [pusher], [sig]); // succeeds — no revert
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation re-established

// 5. Pusher's push now lands in creator's namespace, not pusher's own
vm.prank(pusher);
oracle.call(wordAt(slotId, pos, raw, tsMs));
assertEq(oracle.getOracleData(feedIdOf(creator, slotId, pos)).price, decoded_price); // creator's feed updated
assertEq(oracle.getOracleData(feedIdOf(pusher,   slotId, pos)).price, 0);            // pusher's own feed empty
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L24-40)
```text
contract CompressedOracleV1 is OracleBase, ICompressedOracleV1 {
    /// @notice Oracle family discriminator for off-chain introspection (matches the
    ///         pusher/console `kind` vocabulary).
    string public constant kind = "compressed";

    mapping(address => address) public namespaceRemapping;

    uint256 private constant MAX_PER_SLOT = 4;

    uint256 private constant X48 = 0xFFFFFFFFFFFF;
    uint256 private constant X56 = 0xFFFFFFFFFFFFFF;
    uint256 private constant X80 = 0xFFFFFFFFFFFFFFFFFFFF;

    constructor(address _owner, uint256 maxTimeDrift) OracleBase(_owner, maxTimeDrift) {
        // feedIds reserve 80 bits for the chain id (EIP-2294 caps real ids well below).
        require(block.chainid <= X80);
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
