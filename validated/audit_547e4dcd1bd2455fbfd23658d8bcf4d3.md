The vulnerability is real. Here is the analysis:

**Core issue in `allowPushers`:** The function verifies that pusher B signed consent for `msg.sender` (the calling creator), but performs an **unconditional overwrite** of `namespaceRemapping[pusher]` with no guard against an existing delegation. [1](#0-0) 

The signed digest binds `(chainid, oracle, deadline, pusher, msg.sender)`. When C calls `allowPushers(D2, [B], [sig_B_for_C])`, the recovered signer is B (correct), and `namespaceRemapping[B] = C` overwrites the prior `namespaceRemapping[B] = A` with no check.

The existing test `testHijackWithSignatureForDifferentCreatorReverts` only covers the case where an attacker tries to use a signature B signed *for someone else* — it does not cover the case where C legitimately holds a signature B signed *for C*. [2](#0-1) 

---

### Title
`allowPushers` allows unconditional overwrite of an existing pusher delegation, enabling silent namespace hijack — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
`CompressedOracleV1.allowPushers` does not check whether `namespaceRemapping[pusher]` is already set before writing. Any creator C who holds a valid EIP-191 consent signature from pusher B (signed for C) can call `allowPushers` and atomically remap B from creator A's namespace to C's namespace, without A's knowledge or consent.

### Finding Description
`allowPushers` verifies the pusher's signature over `(chainid, oracle, deadline, pusher, msg.sender)` and then unconditionally writes:

```solidity
namespaceRemapping[pusher] = msg.sender;   // line 209 — no prior-delegation guard
``` [3](#0-2) 

There is no `require(namespaceRemapping[pusher] == address(0))` or equivalent. The attack path:

1. B signs consent for A at deadline D1 → A calls `allowPushers` → `namespaceRemapping[B] = A`.
2. B signs consent for C at deadline D2 > D1 (B may be a multi-creator pusher service, or was socially engineered).
3. C calls `allowPushers(D2, [B], [sig_B_for_C])`. Signature verification passes (B did sign for C). `namespaceRemapping[B] = C` overwrites A's delegation silently.
4. All subsequent `fallback()` pushes from B resolve `creator = namespaceRemapping[msg.sender]` as C, writing into C's namespace instead of A's. [4](#0-3) 

A's feeds stop receiving updates. A cannot detect this on-chain (no event from A's perspective that its pusher was stolen).

### Impact Explanation
A's oracle feeds become permanently stale from the moment of the hijack. Any pool consuming A's feeds via `price(feedId, pool)` will have its `maxTimeDrift` check fail on every swap, making the pool's swap path completely unusable. This satisfies the "broken core pool functionality / unusable swap flows" impact gate.

### Likelihood Explanation
Requires B to have signed a consent message for C. This is realistic when B is a shared pusher service (signs for multiple creators), or when C tricks B into signing a consent off-chain. The deadline mechanism does not prevent this — it only prevents replay after expiry, not concurrent multi-creator consent. The attack is fully on-chain once C holds the signature.

### Recommendation
Before writing `namespaceRemapping[pusher] = msg.sender`, add a guard:

```solidity
address existing = namespaceRemapping[pusher];
require(existing == address(0) || existing == msg.sender, AlreadyDelegated());
```

This forces B to explicitly `revokePusher()` before a new creator can claim them, preserving the single-namespace-binding invariant.

### Proof of Concept

```solidity
// Foundry integration test sketch
function testNamespaceHijack() public {
    uint256 D1 = block.timestamp + 1 days;
    uint256 D2 = block.timestamp + 2 days;

    // B signs consent for A (D1) and for C (D2)
    bytes memory sigForA = _signConsent(PUSHER_B_KEY, D1, pusherB, creatorA);
    bytes memory sigForC = _signConsent(PUSHER_B_KEY, D2, pusherB, creatorC);

    // A registers B
    address[] memory p = new address[](1); p[0] = pusherB;
    bytes[]   memory s = new bytes[](1);   s[0] = sigForA;
    vm.prank(creatorA);
    oracle.allowPushers(D1, p, s);
    assertEq(oracle.namespaceRemapping(pusherB), creatorA);

    // C hijacks B using B's valid consent for C
    s[0] = sigForC;
    vm.prank(creatorC);
    oracle.allowPushers(D2, p, s);

    // namespaceRemapping[B] is now C — A's delegation silently overwritten
    assertEq(oracle.namespaceRemapping(pusherB), creatorC);

    // B's next push lands in C's namespace, not A's
    vm.prank(pusherB);
    (bool ok,) = address(oracle).call(_wordAt(0, 0, someRaw, tsMs));
    assertTrue(ok);
    assertEq(oracle.getOracleData(oracle.feedIdOf(creatorC, 0, 0)).price, expectedPrice);
    assertEq(oracle.getOracleData(oracle.feedIdOf(creatorA, 0, 0)).price, 0); // A's feed dead
}
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracleRegistrationless.t.sol (L143-165)
```text
    function testHijackWithSignatureForDifferentCreatorReverts() public {
        uint256 victimKey = 0xBEEF;
        address victim = vm.addr(victimKey);
        address attacker = address(0xA77ACc);
        address otherCreator = address(0xFEED);
        uint256 deadline = block.timestamp + 1 days;

        // Victim signed consent to be a pusher for `otherCreator`, NOT for the attacker.
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, victim, otherCreator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(victimKey, digest);

        address[] memory pushers = new address[](1);
        pushers[0] = victim;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = abi.encodePacked(r, s, v);

        // Attacker replays it in their own namespace: recovered signer != victim → revert.
        vm.prank(attacker);
        vm.expectRevert();
        oracle.allowPushers(deadline, pushers, sigs);
    }
```
