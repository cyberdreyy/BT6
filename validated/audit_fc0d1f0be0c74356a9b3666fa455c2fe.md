The vulnerability is real. Let me confirm the exact mechanics before writing the finding.

**Key facts from the code:**

1. `allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)` — `msg.sender` is the creator, so the signature is creator-specific. [1](#0-0) 

2. There is **no nonce, no used-signature bitmap, no revocation flag** — confirmed by grep across all `.sol` files. The only replay protection is the deadline.

3. `revokePusher` sets `namespaceRemapping[msg.sender] = address(0)` but does not invalidate any previously issued signatures. [2](#0-1) 

4. The code comment at lines 186–191 explicitly states the deadline is meant to prevent re-establishment after revocation ("an undated signature could re-establish a delegation AFTER the pusher revoked it"), but the deadline only blocks replay *after* it expires — not within the window. [3](#0-2) 

**Correction to the question's premise**: The question claims "any caller" can replay. This is incorrect. The signature commits to `msg.sender` (the creator), so only the **original creator** can replay it — a third-party attacker cannot redirect the pusher into a different namespace. The replay is creator-only.

---

### Title
Creator Can Replay Revoked Pusher's EIP-191 Consent Within Deadline Window, Re-establishing Delegation Without Current Consent — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`allowPushers` has no nonce or used-signature tracking. After a pusher calls `revokePusher`, the original creator can call `allowPushers` again with the identical `(deadline, pusher, signature)` tuple before the deadline expires, atomically re-establishing `namespaceRemapping[pusher] = creator` without any new consent from the pusher.

### Finding Description
`revokePusher` clears `namespaceRemapping[msg.sender]` to `address(0)` but does not invalidate the pusher's previously issued EIP-191 signature. [2](#0-1) 

`allowPushers` only checks:
- `_ensureDeadline(deadline)` — passes if the original deadline has not yet expired
- ECDSA recovery matches the pusher — passes because the signature is still cryptographically valid [4](#0-3) 

There is no check that the pusher has not previously revoked, and no mechanism to mark a signature as consumed. The code comment at lines 186–191 acknowledges the deadline is the sole guard against post-revocation replay, but the deadline only prevents replay *after* it expires, not within the window. [3](#0-2) 

### Impact Explanation
After the creator replays the signature:
- `namespaceRemapping[pusher] = creator` is restored
- The pusher, believing they have revoked, continues pushing data they intend for their own namespace
- Those pushes are silently redirected into the creator's namespace
- If the creator's namespace feeds a pool's `AnchoredPriceProvider`, the misdirected data (potentially stale, wrong asset, or wrong scale) reaches pool swap price computation without the pusher's knowledge or intent

This satisfies the **bad-price execution** impact gate: stale or wrong price data reaches a pool swap because the delegation guard was bypassed.

### Likelihood Explanation
Requires the creator to act adversarially against their own pusher within the deadline window. Deadlines are typically set to hours or days, giving a meaningful replay window. The pusher has no on-chain way to detect the re-establishment without monitoring events.

### Recommendation
Track consumed signatures with a `mapping(bytes32 => bool) private _usedConsents` keyed on `keccak256(abi.encode(deadline, pusher, creator))`. Mark it `true` on first use in `allowPushers` and revert on replay. Alternatively, introduce a per-pusher nonce that the pusher increments on revocation, binding the signature to a specific delegation epoch.

### Proof of Concept
```solidity
// 1. Pusher signs consent
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator replays the SAME signature before deadline — succeeds, no revert
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation re-established

// 5. Pusher pushes stale data thinking it goes to own namespace
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(slotId, pos, staleRaw, staleTs));
assertTrue(ok);

// 6. Stale data lands in creator's namespace, not pusher's
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, slotId, pos)).price, U64x32.decode(staleRaw >> 16));
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,   slotId, pos)).price, 0);
```

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
