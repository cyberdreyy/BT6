Based on my investigation of the `CompressedOracle.sol` pusher delegation code, I found a valid analog to the Pod.sol frontrunning vulnerability.

---

### Title
`allowPushers` Nonce-less Signature Allows Creator to Replay Revoked Consent and Re-Hijack Pusher Namespace — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

The `allowPushers` function uses a deadline-bound EIP-191 signature with no per-pusher nonce. After a pusher calls `revokePusher()` to clear their delegation, the creator retains the original signed consent and can replay it — before the deadline expires — to silently re-establish `namespaceRemapping[pusher] = creator`. The pusher's subsequent fallback pushes (now intended for their own namespace or a new creator's namespace) are re-routed into the original creator's feed, injecting wrong prices into any pool consuming that feed.

### Finding Description

`allowPushers` signs over `(chainid, oracle, deadline, pusher, creator)`: [1](#0-0) 

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));

namespaceRemapping[pusher] = msg.sender;
```

There is no nonce in the signed payload. The same `(deadline, pusher, creator)` tuple is valid for every call to `allowPushers` until `block.timestamp > deadline`.

`revokePusher()` clears the mapping: [2](#0-1) 

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

But the creator still holds the original signature. Calling `allowPushers` again with the same `deadline` and `sig` passes `_ensureDeadline` and the `ECDSA.recover` check identically, writing `namespaceRemapping[pusher] = creator` again — overwriting any subsequent delegation the pusher may have established with a different creator.

The code comment acknowledges the deadline prevents re-establishment *after* expiry, but is silent on re-establishment *before* expiry: [3](#0-2) 

> "an undated signature could re-establish a delegation AFTER the pusher revoked it."

This only guards the post-expiry case. The pre-expiry replay window — which can be days or weeks — is unguarded.

The fallback push path resolves the namespace at push time: [4](#0-3) 

Any push made by the pusher after revocation — including pushes for a different asset or a new creator — lands in the replayed creator's namespace instead of the intended one.

### Impact Explanation

After the replay, the pusher's fallback pushes are attributed to the original creator's feed. If the pusher has since begun pushing data for a different asset (e.g., ETH/USD after revoking from a BTC/USD creator), those prices overwrite the creator's BTC/USD slot. `AnchoredPriceProvider` and `PriceProvider` consume the creator's feed without knowledge of the namespace hijack, delivering the wrong mid/spread to `MetricOmmPool.swap`. This satisfies the **bad-price execution** impact gate: an inverted or wrong-asset bid/ask quote reaches a live pool swap, enabling a trader to extract value from LPs or causing pool insolvency if the price deviation is large.

Additionally, if the pusher had re-delegated to Creator B, the replay overwrites `namespaceRemapping[pusher] = creatorA`, silently starving Creator B's feed of updates and making it stale.

### Likelihood Explanation

- The creator retains the original `sig` bytes from the initial `allowPushers` call — no additional off-chain capability is needed.
- Deadlines are typically set days to weeks in the future (as shown in tests using `block.timestamp + 1 days`), giving a large replay window.
- The pusher has no on-chain mechanism to invalidate the signature before the deadline expires.
- The attack requires only a standard `allowPushers` call — no privileged role, no special setup. [5](#0-4) 

### Recommendation

Add a per-pusher nonce to the signed digest and increment it on every successful `allowPushers` and on `revokePusher`:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]++
    ))
);

// In revokePusher:
pusherNonce[msg.sender]++;   // invalidates any outstanding signed consent
namespaceRemapping[msg.sender] = address(0);
```

This ensures that once a pusher revokes, every previously issued signature is cryptographically invalidated regardless of its deadline.

### Proof of Concept

1. Creator A calls `allowPushers(deadline=T, [pusher], [sigA])` — `namespaceRemapping[pusher] = creatorA`.
2. Pusher calls `revokePusher()` — `namespaceRemapping[pusher] = address(0)`.
3. Pusher signs new consent for Creator B and Creator B calls `allowPushers` — `namespaceRemapping[pusher] = creatorB`.
4. Creator A calls `allowPushers(deadline=T, [pusher], [sigA])` again with the **identical** `sigA` (T not yet expired).
5. `_ensureDeadline(T)` passes; `ECDSA.recover` returns `pusher`; `namespaceRemapping[pusher] = creatorA` — Creator B's delegation silently overwritten.
6. Pusher's subsequent fallback pushes (ETH/USD data intended for Creator B's feed) land in Creator A's BTC/USD slot.
7. `AnchoredPriceProvider.getBidAndAskPrice` returns ETH/USD prices for a BTC/USD pool.
8. Pool swap executes at the wrong price; LPs suffer direct principal loss. [6](#0-5)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L262-266)
```text
    /*
     *
     * Push paths
     *
     */
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L361-370)
```text

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        vm.prank(creator);
        vm.expectRevert(IOffchainOracle.DeadlineExceeded.selector);
        oracle.allowPushers(deadline, pushers, sigs);
    }
```
