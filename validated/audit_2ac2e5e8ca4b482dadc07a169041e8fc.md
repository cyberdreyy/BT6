### Title
Pusher Delegation Signature Replay Undoes Revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers()` in `CompressedOracleV1` accepts an EIP-191 pusher-consent signature that commits to `(chainid, oracle, deadline, pusher, creator)` but includes **no nonce**. After a pusher self-revokes via `revokePusher()`, or after the creator removes a pusher via `removePushers()`, the original signature remains cryptographically valid until its deadline expires. The creator can replay it to silently re-establish the revoked delegation, allowing a compromised or unwanted pusher to resume writing prices into the creator's namespace.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The revocation paths zero out `namespaceRemapping[pusher]`:

```solidity
// pusher self-revoke
namespaceRemapping[msg.sender] = address(0);

// creator-initiated removal
namespaceRemapping[pusher] = address(0);
``` [2](#0-1) [3](#0-2) 

Because the signed message contains no nonce and no "used-signature" registry exists, calling `allowPushers` a second time with the identical `(deadline, pusher, signature)` tuple passes all checks and writes `namespaceRemapping[pusher] = creator` again, fully undoing the revocation.

The code's own comment acknowledges the deadline as the only replay mitigation:

> "the deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [4](#0-3) 

But the deadline only prevents replay *after* it expires — it does nothing within the deadline window, which is the exact period during which revocation is most urgent.

---

### Impact Explanation

Once the delegation is re-established, the pusher (or an attacker holding the pusher's key) can call the `fallback()` push path and write arbitrary slot words into the creator's namespace:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
// ... writes into creator's namespace
_writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
``` [5](#0-4) 

The only freshness gate on pushed data is a monotonicity check on the embedded timestamp. An attacker who controls the pusher key can craft a slot word with a timestamp slightly ahead of the current stored value and any price/spread values they choose, passing the monotonicity check:

```solidity
bool newer = timestampMs.isAfter(oldTimestampMs);
if (!newer) continue;
``` [6](#0-5) 

Pools consuming these feeds via `AnchoredPriceProvider` or `PriceProvider` will receive the attacker-controlled bid/ask, enabling bad-price execution: traders receive more than the oracle curve permits, or LPs suffer losses from mispriced swaps.

---

### Likelihood Explanation

The scenario requires:
1. A pusher's key is compromised (realistic for off-chain hot-wallet pushers).
2. The pusher self-revokes via `revokePusher()` to stop the attacker.
3. The creator (or an automated delegation-management bot) replays the old signature before the deadline — either unknowingly or because the bot re-issues `allowPushers` on any detected revocation.

Deadlines are set by the creator and may be far in the future (the code imposes no maximum). The window between revocation and deadline expiry can be hours or days, making replay practically feasible.

---

### Recommendation

Record each consumed signature hash in a `mapping(bytes32 => bool) usedSignatures` and revert if the hash has already been processed:

```solidity
mapping(bytes32 => bool) private _usedDelegationSigs;

// inside allowPushers, after recovering the signer:
require(!_usedDelegationSigs[hash], SignatureAlreadyUsed());
_usedDelegationSigs[hash] = true;
```

Alternatively, include a per-pusher nonce in the signed digest (`keccak256(abi.encode(..., nonces[pusher]++))`) and increment it on every successful `allowPushers` call, so any previously issued signature is immediately invalidated.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with a far-future deadline
uint256 deadline = block.timestamp + 7 days;
bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
(uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
bytes memory sig = abi.encodePacked(r, s, v);

// 2. Creator delegates pusher
address[] memory pushers = new address[](1); pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1); sigs[0] = sig;
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher self-revokes (key compromised)
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator replays the SAME signature — revocation undone
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // succeeds, no revert
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation restored

// 5. Attacker (holding pusher key) pushes a manipulated price into creator's namespace
uint56 tsMs = uint56(block.timestamp * 1000 + 1);
// craft slot word with attacker-chosen price
bytes memory payload = ...; // arbitrary price, valid timestamp
vm.prank(pusher); // attacker controls pusher key
(bool ok,) = address(oracle).call(payload);
assertTrue(ok); // bad price now live in creator's namespace
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-192)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-343)
```text
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
```
