### Title
`allowPushers` delegation signature has no nonce — creator can replay pusher's consent within the deadline window to nullify `revokePusher()` - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` uses a deadline to prevent signature replay, but the deadline only blocks replay **after** it expires. Within the deadline window, the same EIP-191 pusher-consent signature can be submitted again by the creator to re-establish a delegation the pusher already revoked via `revokePusher()`. The code's own NatSpec explicitly acknowledges this risk but the chosen mitigation is incomplete.

---

### Finding Description

`allowPushers` computes the consent hash as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
```

and validates it with:

```solidity
_ensureDeadline(deadline);   // only: block.timestamp <= deadline
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is **no nonce** and **no used-signature bitmap**. The `_ensureDeadline` check only enforces `block.timestamp <= deadline`: [2](#0-1) 

The NatSpec comment on `allowPushers` explicitly states the deadline is the sole guard against post-revocation replay:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

But the deadline only prevents replay **after** it expires. Within the window `[now, deadline]`, the identical `(deadline, pusher, creator, sig)` tuple is accepted an unlimited number of times. A creator can therefore call `allowPushers` again with the original on-chain signature immediately after the pusher calls `revokePusher()`, restoring `namespaceRemapping[pusher] = creator` and making the revocation a no-op.

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [4](#0-3) 

But `allowPushers` unconditionally overwrites it back:

```solidity
namespaceRemapping[pusher] = msg.sender;
``` [5](#0-4) 

The signature is public calldata from the original `allowPushers` transaction, so the creator (or anyone who observed that transaction and can call as the creator) can replay it at zero cost.

---

### Impact Explanation

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [6](#0-5) 

If the pusher's key is compromised and the attacker is pushing bad prices into the creator's namespace, the pusher calls `revokePusher()` as an emergency stop. The creator (acting in good faith or maliciously) replays the original consent signature, re-establishing the delegation. The attacker's pushes resume landing in the creator's namespace.

Those feeds are consumed by `AnchoredPriceProvider.getBidAndAskPrice()`: [7](#0-6) 

which is called during pool swaps. Bad mid/spread values written by the attacker propagate directly to live bid/ask quotes, causing **bad-price execution** — traders receive more output than the oracle permits or the pool receives less input than owed.

---

### Likelihood Explanation

- The pusher's consent signature is permanently visible on-chain from the first `allowPushers` call.
- The creator is the only party who can replay it (the hash binds `msg.sender`), so the trigger requires the creator to be malicious **or** to call `allowPushers` again without realising the pusher's key is compromised (e.g., automated re-delegation scripts).
- Deadlines are typically set days to weeks in the future, leaving a large replay window.
- Likelihood: **Medium** — requires a specific creator action, but the window is wide and the signature is permanently public.

---

### Recommendation

Track consumed signatures with a per-pusher nonce or a `mapping(bytes32 => bool) usedSignatures` bitmap, and include the nonce in the signed hash:

```solidity
// storage
mapping(address => uint256) public pusherNonce;

// in allowPushers
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]++   // ← consume the nonce
    ))
);
```

This makes every consent signature single-use: once the delegation is established (or revoked and re-established), the nonce advances and the old signature is permanently invalid.

---

### Proof of Concept

```
T=0   Pusher signs: keccak256(chainid, oracle, deadline=T+30d, pusher, creator)  → sig
T=1   creator calls allowPushers(T+30d, [pusher], [sig])
        → namespaceRemapping[pusher] = creator  ✓

T=2   Pusher's key is compromised; attacker pushes bad prices via fallback()
        → bad data lands in creator's namespace

T=3   Pusher calls revokePusher()
        → namespaceRemapping[pusher] = address(0)  ✓ (attacker's pushes now go to pusher's own ns)

T=4   Creator calls allowPushers(T+30d, [pusher], [sig])  ← SAME signature, still within deadline
        → namespaceRemapping[pusher] = creator  ← revocation nullified

T=5   Attacker continues pushing bad prices into creator's namespace
        → AnchoredPriceProvider.getBidAndAskPrice() returns attacker-controlled bid/ask
        → pool swap executes at bad price
```

The replay at T=4 succeeds because `_ensureDeadline` only checks `block.timestamp <= deadline` and there is no nonce or used-signature guard. [8](#0-7) [2](#0-1)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```
