### Title
`allowPushers` Consent Signature Has No Replay Guard, Allowing Creator to Override Pusher's Revocation and Re-Enable Compromised Price Injection — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` accepts a pusher's EIP-191 consent signature but stores no record of used signatures and tracks no per-pusher nonce. The only replay bound is the deadline. After a pusher calls `revokePusher()`, the creator can immediately re-submit the same old signature (deadline still valid) and restore `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently overridden. If the pusher revoked because their key was compromised, the creator's re-establishment lets the attacker continue pushing manipulated prices into the creator's namespace — prices that pools backed by the CompressedOracle will consume at swap time.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no `mapping(bytes32 => bool) usedSignatures`, and no per-pusher counter. The contract's own NatSpec acknowledges the gap: *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it"* — and names the deadline as the mitigation. [2](#0-1) 

But the deadline only bounds the replay window; it does not prevent the same bytes from being submitted a second time within that window. `revokePusher()` writes `namespaceRemapping[msg.sender] = address(0)`: [3](#0-2) 

Because `allowPushers` performs no used-signature check, the creator can call it again with the identical `(deadline, [pusher], [sig])` tuple and restore `namespaceRemapping[pusher] = creator` in the very next block. The revocation is dead code within the deadline window — the exact structural analog to the external report's `onConflictDoNothing` on a table with no unique constraint.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push from the compromised pusher key after the creator re-establishes the delegation lands in the creator's namespace, overwriting legitimate price data with attacker-controlled values.

The `CompressedOracleV1.price` path is consumed by `AnchoredPriceProvider` (and any provider that sets `offchainOracle` to the compressed oracle) at swap time: [5](#0-4) 

A manipulated mid price passes through `_readLeg` → `_computeBidAsk` → `getBidAndAskPrice` and is handed to the pool's swap math as the canonical bid/ask.

---

### Impact Explanation

A compromised pusher key can inject an arbitrary `U64x32`-encoded price into any slot in the creator's namespace. The `AnchoredPriceProvider` clamps the quote to `mid ± (spreadBps + minMargin)`, but `mid` itself comes from the oracle — a manipulated mid shifts the entire band. Traders swapping against the pool receive execution at the attacker-set price; LPs absorb the loss. The `CompressedOracleV1` is open (no in-swap binding, reads permissionless), so any pool wired to it is exposed.

---

### Likelihood Explanation

Medium. The creator must actively call `allowPushers` a second time after the pusher revokes. This is plausible when: (a) the creator's monitoring does not distinguish a security-motivated revocation from a transient one, (b) the creator's tooling automatically re-establishes pushers that drop off, or (c) the deadline window is long (hours to days). The pusher's only recourse after re-establishment is to stop pushing entirely, but the attacker holding the compromised key is not bound by that decision.

---

### Recommendation

Invalidate each consent signature on first use:

```solidity
mapping(bytes32 => bool) private _usedConsentSigs;

// inside allowPushers, after ECDSA.recover succeeds:
bytes32 sigId = keccak256(signatures[i]);
require(!_usedConsentSigs[sigId], "consent already consumed");
_usedConsentSigs[sigId] = true;
```

Alternatively, include a per-pusher nonce in the signed payload so each consent is single-use by construction:

```solidity
mapping(address => uint256) public pusherNonce;

keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]++))
```

Either change makes `revokePusher()` final: the old signature is consumed and cannot restore the delegation.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent (deadline = now + 1 day)
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes (key compromised)
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator re-submits the SAME signature — succeeds, deadline still valid
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // revocation overridden

// 5. Attacker (holds compromised pusher key) pushes a manipulated price
uint48 badPrice = _packRaw(999_999_999, 0, 0); // extreme price
vm.prank(pusher);                               // attacker controls this key
(bool ok,) = address(oracle).call(_wordAt(0, 0, badPrice, uint56(block.timestamp * 1000)));
assertTrue(ok);

// 6. Pool reads the manipulated price via AnchoredPriceProvider → bad bid/ask → swap loss
IOffchainOracle.OracleData memory d = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
assertEq(d.price, U64x32.decode(uint32(badPrice >> 16))); // attacker's value is live
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-271)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
```
