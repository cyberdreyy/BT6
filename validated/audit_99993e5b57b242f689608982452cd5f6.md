### Title
Creator can replay pusher consent signature to bypass `revokePusher()`, silently re-delegating namespace writes — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` accepts any EIP-191 consent signature that has not yet expired. There is no per-signature nonce and no revocation record. After a pusher calls `revokePusher()`, the creator can immediately replay the original consent bytes (while the deadline is still in the future) to restore `namespaceRemapping[pusher] = creator`, silently undoing the revocation.

---

### Finding Description

`allowPushers` binds the signed consent to:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no "used-signature" bitmap, and no revocation timestamp stored anywhere. The only replay barrier is the deadline itself.

`revokePusher()` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But this write has no effect on the validity of any previously issued signature. A creator who holds the original consent bytes can call `allowPushers` again with the identical `deadline`, `pushers`, and `signatures` arguments, restoring `namespaceRemapping[pusher] = creator` without any new consent from the pusher.

The analog to the Olympus bug is exact: just as `_setModuleRiskLevel()` was a security-configuration action that was itself unprotected, `revokePusher()` is a security-revocation action whose effect can be silently undone by replaying an already-consumed credential.

---

### Impact Explanation

A pusher who revokes (e.g., after discovering the creator is malicious, or after a key-rotation) can be silently re-delegated. Any subsequent fallback pushes from that pusher land in the creator's namespace rather than the pusher's own namespace. [3](#0-2) 

If the pusher is unaware of the re-delegation and continues to push prices, the creator's pool consumes those prices. Because the creator's namespace may not carry the same `priceGuard` bounds that the pusher configured for their own namespace, the prices can reach live swaps unclamped. This satisfies the **bad-price execution** and **admin-boundary break** impact criteria: an oracle role check (`revokePusher`) is bypassed by a semi-trusted path (the creator replaying a credential), and unclamped quotes can reach pool swaps.

---

### Likelihood Explanation

**Medium.** The creator must hold the original consent signature bytes and the deadline must not yet have expired. Both conditions are routinely true: pushers typically sign long-lived consents (days or weeks) so that the creator does not need to re-collect signatures frequently. The window between a pusher's revocation and the deadline expiry is therefore often large.

---

### Recommendation

Add a per-pusher revocation nonce or a `revokedAt` timestamp. In `allowPushers`, require that the signed `deadline` is strictly greater than the pusher's last revocation timestamp:

```solidity
mapping(address pusher => uint256 revokedAt) public revokedAt;

// in revokePusher():
revokedAt[msg.sender] = block.timestamp;

// in allowPushers(), after signature recovery:
require(deadline > revokedAt[pusher], "signature predates revocation");
```

Alternatively, include a monotonic per-pusher nonce in the signed message and increment it on every `revokePusher` call, making all prior signatures immediately invalid.

---

### Proof of Concept

```
T+0:      Pusher signs: keccak256(abi.encode(chainid, oracle, deadline=T+1day, pusher, creator))
T+0:      Creator calls allowPushers(T+1day, [pusher], [sig])
          → namespaceRemapping[pusher] = creator  ✓

T+0.5day: Pusher calls revokePusher()
          → namespaceRemapping[pusher] = address(0)  (pusher believes they are free)

T+0.8day: Creator calls allowPushers(T+1day, [pusher], [sig])  ← SAME sig, deadline still valid
          → _ensureDeadline passes (T+1day > T+0.8day)
          → ECDSA.recover returns pusher  (signature is still cryptographically valid)
          → namespaceRemapping[pusher] = creator  ← revocation silently undone

T+0.8day+: Pusher continues to push prices believing they write to their own namespace.
           All fallback writes resolve to creator's namespace.
           Creator's pool reads those prices without the pusher's priceGuard bounds.
           Unclamped bid/ask quote reaches live swap.
``` [3](#0-2) [4](#0-3)

### Citations

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L236-243)
```text
    /// @notice Allows a pusher to self-revoke their delegation. After revocation the
    ///         wallet pushes into its OWN namespace again (the registrationless default).
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```
