### Title
Pusher Delegation Signature Replay Bypasses `revokePusher()` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` commits the pusher's EIP-191 consent to `(chainid, oracle, deadline, pusher, creator)` but includes **no nonce**. Because the signed message is stateless, a creator can replay the same pusher signature an unlimited number of times before the deadline expires — including immediately after the pusher self-revokes via `revokePusher()`. The revocation mechanism is therefore fully bypassable by the creator, and the pusher's price pushes continue to be silently redirected into the creator's namespace against the pusher's will.

---

### Finding Description

`allowPushers` constructs the hash that the pusher must sign as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The five fields bound the signature to a specific chain, oracle contract, deadline window, pusher wallet, and creator. There is no per-use nonce or any on-chain consumed-flag. The function writes `namespaceRemapping[pusher] = msg.sender` and emits an event, but it never marks the signature as spent. [2](#0-1) 

`revokePusher()` zeroes the mapping entry:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But because the original signature is still cryptographically valid (the deadline has not changed, no nonce was consumed), the creator can immediately call `allowPushers` again with the identical `(deadline, [pusher], [signature])` arguments. The mapping is re-written to `creator`, and the revocation is undone. This cycle can repeat indefinitely until `block.timestamp > deadline`.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after the forced re-delegation lands in the creator's oracle slots, not the pusher's own namespace.

---

### Impact Explanation

Any pool whose `AnchoredPriceProvider` or `CompressedOracle` price path reads a feed owned by the creator will receive prices originating from a pusher who explicitly revoked consent. If the pusher has since changed their data format, stopped updating, or is pushing for a different asset, the creator's feed will carry stale, mismatched, or manipulated bid/ask values. Pools consuming that feed execute swaps at a bad price, violating the swap-conservation and oracle-freshness invariants. LP principal is at risk if the bad price persists long enough for arbitrageurs to drain the mispriced side.

---

### Likelihood Explanation

Requires a creator who retains the pusher's original signature and is willing to act adversarially. Deadlines are caller-supplied and can be set years in the future. A pusher who signs a long-lived consent (common for operational convenience) has no on-chain recourse until the deadline expires. The pusher cannot shorten the deadline retroactively. Likelihood is **Low-to-Medium**: the setup is realistic for any production deployment where pushers sign broad delegations.

---

### Recommendation

Add a per-pusher nonce to the signed message and store it on-chain. Increment it on every successful `allowPushers` call and on every `revokePusher` call. The pusher's signature must commit to the current nonce value; replaying an old signature with a stale nonce will fail.

```solidity
// storage
mapping(address => uint256) public pusherNonce;

// in allowPushers, per pusher:
uint256 currentNonce = pusherNonce[pusher]++;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, currentNonce))
);

// in revokePusher:
pusherNonce[msg.sender]++;   // invalidates any outstanding signed consent
namespaceRemapping[msg.sender] = address(0);
```

Incrementing the nonce inside `revokePusher` ensures that any signature the pusher issued before revoking is permanently invalidated, mirroring the sequential-nonce fix recommended in the external report.

---

### Proof of Concept

```
1. Pusher P signs consent for Creator C with deadline = block.timestamp + 365 days.
   Signed message: keccak256(chainid, oracle, deadline, P, C)

2. Creator C calls allowPushers(deadline, [P], [sig])
   → namespaceRemapping[P] = C  ✓

3. Pusher P calls revokePusher()
   → namespaceRemapping[P] = address(0)  ✓ (pusher believes they are free)

4. Creator C immediately calls allowPushers(deadline, [P], [sig])  ← SAME sig
   → require(P == ECDSA.recover(hash, sig)) passes (hash unchanged, sig unchanged)
   → namespaceRemapping[P] = C  ← revocation undone

5. Pusher P calls fallback() to push prices to their own namespace.
   → creator = namespaceRemapping[P] = C  (not address(0))
   → prices land in Creator C's oracle slots

6. Pools reading Creator C's feeds execute swaps at P's (possibly stale/wrong) prices.
   Steps 4–6 repeat until deadline expires.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L209-210)
```text
            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-242)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```
