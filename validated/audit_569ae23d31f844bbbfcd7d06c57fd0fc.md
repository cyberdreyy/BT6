### Title
Pusher Revocation Bypass via Signed Consent Replay in `allowPushers` — (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary
A pusher's `revokePusher()` call can be bypassed by the creator replaying the original EIP-191 signed consent before the deadline expires, silently re-establishing the delegation against the pusher's will and allowing a compromised pusher to continue injecting prices into the creator's namespace.

### Finding Description
`allowPushers` accepts a pusher's signed consent that covers `(chainid, oracle, deadline, pusher, creator)` and writes `namespaceRemapping[pusher] = msg.sender`. [1](#0-0) 

The only replay guard is the deadline: `_ensureDeadline(deadline)` rejects calls after the deadline, and the comment explicitly states this is the mechanism that prevents re-establishing a delegation after the pusher revoked it. [2](#0-1) 

However, the deadline only prevents replay **after** it expires. Within the deadline window the signed consent is a reusable token: there is no nonce, no one-time-use flag, and no check that `namespaceRemapping[pusher]` is currently zero before overwriting it. After a pusher calls `revokePusher()` and clears the mapping to `address(0)`: [3](#0-2) 

the creator can immediately call `allowPushers` again with the **identical** `(deadline, pusher, sig)` tuple. `_ensureDeadline` passes (deadline not yet expired), ECDSA recovery succeeds (same signature, same inputs), and `namespaceRemapping[pusher]` is overwritten back to the creator. The pusher's revocation is silently undone.

### Impact Explanation
The `revokePusher` mechanism is the pusher's only unilateral safety valve. If a pusher key is compromised and the legitimate key-holder calls `revokePusher()` to stop bad prices from flowing into the creator's namespace, the creator (or an attacker who has also obtained the creator key) can replay the original consent and re-establish the delegation before the deadline expires. The compromised pusher then continues to push arbitrary prices into the creator's namespace. Those prices are consumed by `AnchoredPriceProvider._readLeg` via `CompressedOracleV1.price`, and if the pushed mid is within the priceGuard bounds and the timestamp is fresh, the bad price reaches `_computeBidAsk` and is returned to the pool as a live bid/ask quote — a direct bad-price execution impact. [4](#0-3) 

### Likelihood Explanation
The creator retains the signed consent bytes from the initial `allowPushers` call (they submitted the transaction). Re-establishing the delegation requires only replaying that same calldata before the deadline. No new signature is needed. The window is as wide as the original deadline (up to any value the creator chose). A creator who is unaware the pusher was compromised may replay the consent in good faith, inadvertently re-enabling the bad-price path.

### Recommendation
Include a per-pusher nonce in the signed message and increment it on each successful `allowPushers` call, or record a `revokedAt` timestamp per pusher and reject any consent signed before that timestamp. The simplest fix is to add a `mapping(address => uint256) public pusherNonce` and include `pusherNonce[pusher]++` in the signed payload, invalidating all prior consents on revocation.

### Proof of Concept
```
// 1. Pusher signs consent for creatorA with deadline D1 (e.g. block.timestamp + 1 days)
bytes memory sig = pusher.sign(keccak256(abi.encode(chainid, oracle, D1, pusher, creatorA)));

// 2. CreatorA establishes delegation
vm.prank(creatorA);
oracle.allowPushers(D1, [pusher], [sig]);
// namespaceRemapping[pusher] == creatorA ✓

// 3. Pusher self-revokes (e.g. key compromise detected)
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 4. CreatorA replays the SAME signed consent (D1 not yet expired)
vm.prank(creatorA);
oracle.allowPushers(D1, [pusher], [sig]);   // no revert — deadline still valid, sig still valid
// namespaceRemapping[pusher] == creatorA again — revocation bypassed

// 5. Compromised pusher pushes bad price into creatorA's namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw  = _packRaw(BAD_PRICE, 5, 0);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(slotId, pos, raw, tsMs));
// ok == true; bad price lands in creatorA's feed, consumed by AnchoredPriceProvider
``` [5](#0-4) [6](#0-5)

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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```
