### Title
Claims in `MerkleClaim` can be permanently bricked by a blocked/non-canonical token entrypoint because the payout token address is fixed at initialization - (File: contracts/MerkleClaim.sol)

### Summary
The Footium report shows a claim contract that keys all payout logic to a single token address captured in the Merkle root; if that token address's entrypoint is later disabled (double-entry token, e.g. the real TUSD case, >22 months and counting), every claim reverts and funds are stuck. `MerkleClaim` in this repo has the same structural flaw, made worse by the fact that its only rescue function (`sweep`) also routes through the same fixed `token` address and the same strict `IERC20` interface.

### Finding Description
`MerkleClaim.initialize` stores `token` once and it can never be changed (`require(token == address(0))` guards re-init) [1](#0-0) . Every subsequent payout goes through that exact stored address:

- `claim` calls `IERC20Upgradeable(token).transfer(to, amount)` [2](#0-1) 
- `sweep` calls `IERC20Upgradeable(_token).transfer(to, ...)` on the same stored `token` [3](#0-2) 

Two consequences:

1. **Double-entry token (the external analog).** If the distributed token is or becomes a two-address token and the entrypoint stored in `token` is disabled at the token level (the TUSD precedent cited in the report), `transfer` reverts on every `claim`. Unlike Footium there is no alternate claim path — and critically `sweep` also reverts, so the TL multisig cannot recover the contract's balance even after the 60-day window. The funds are locked indefinitely.

2. **Non-bool-returning tokens.** `IERC20Upgradeable.transfer` expects a `bool` return; tokens like USDT return nothing, so the ABI decode reverts on every `claim` and every `sweep` from day one. Idle's credit vaults settle in USDC/USDT-like stablecoins, so initializing a claim round with such a token is realistic; the code itself does not use `safeTransfer`, which would tolerate the missing return value.

The accounting (Merkle leaf `(to, amount)`, `hasClaimed`) is independent of the token address, so the fix is trivial — but as written, a blocked or non-compliant token entrypoint turns every claim into a revert with no escape hatch.

### Impact Explanation
All tokens held by the `MerkleClaim` instance become unclaimable: every claimee's distribution is DoS'd, and because `sweep` shares the same vulnerable path, there is no privileged recovery either. This is a permanent (or at minimum ≥1 year, matching the TUSD timeline in the referenced report) freeze of distributed funds with potential USD-value loss.

### Likelihood Explanation
Triggering the double-entry scenario requires an external token upgrade, which is speculative — but the repo's own history shows these contracts are used for real token distributions, and the TUSD incident demonstrates it happens in practice. The non-compliant `transfer` return variant requires no external event at all: it is deterministic if the claim is initialized against USDT-class tokens. No privileged misbehavior is needed in either case.

### Recommendation
- Use `SafeERC20Upgradeable.safeTransfer` in both `claim` and `sweep` so non-bool-returning tokens work.
- Add an owner/multisig `setToken`/rescue path (or make `sweep` accept a token address parameter) so a disabled entrypoint does not brick recovery; alternatively route claims through a canonical token address whitelist as suggested in the external report.

### Proof of Concept
Foundry sketch (variant 2 — deterministic, no external token upgrade needed):

```solidity
// test/foundry/MerkleClaimStuck.t.sol
function testClaimRevertsOnNonBoolToken() external {
    // USDT-like token: transfer() returns nothing
    NonBoolERC20 usdt = new NonBoolERC20();
    MerkleClaim mc = new MerkleClaim();

    address user = makeAddr("user");
    uint256 amount = 100e6;
    bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(user, amount))));
    bytes32 root = leaf; // single-leaf tree
    mc.initialize(root, address(usdt));
    usdt.mint(address(mc), amount);

    bytes32[] memory proof = new bytes32[](0);
    vm.expectRevert(); // abi.decode of missing bool return reverts
    mc.claim(user, amount, proof);

    // Rescue path is broken too:
    vm.warp(block.timestamp + 61 days);
    vm.prank(0xFb3bD022D5DAcF95eE28a6B07825D4Ff9C5b3814);
    vm.expectRevert();
    mc.sweep(makeAddr("treasury"));
}
```

For the double-entry variant, deploy a mock token exposing two proxy addresses that forward to one logic contract; initialize `MerkleClaim` with proxy A, then flip a flag so proxy A reverts on `transfer` (mirroring `0x8dd5fbCe2F6a956C3022bA3663759011Dd51e73E` being disabled on mainnet). `claim` and `sweep` both revert permanently even though the contract holds the full balance.

### Citations

**File:** contracts/MerkleClaim.sol (L37-44)
```text
  function initialize(bytes32 _merkleRoot, address _token) public {
    require(token == address(0)); // Ensure token is not set

    merkleRoot = _merkleRoot; // Update root
    token = _token;

    deployTime = block.timestamp;
  }
```

**File:** contracts/MerkleClaim.sol (L52-66)
```text
  function claim(address to, uint256 amount, bytes32[] calldata proof) external {
    // Throw if address has already claimed tokens
    if (hasClaimed[to]) revert AlreadyClaimed();

    // Verify merkle proof, or revert if not in tree,
    // double keccak is preferred https://github.com/OpenZeppelin/merkle-tree#user-content-fn-1-786ed85226485d53b8a7834b144c1642
    bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(to, amount))));
    bool isValidLeaf = MerkleProofUpgradeable.verify(proof, merkleRoot, leaf);
    if (!isValidLeaf) revert NotInMerkle();

    // Set address to claimed
    hasClaimed[to] = true;

    // Transfer tokens to claimee
    IERC20Upgradeable(token).transfer(to, amount);
```

**File:** contracts/MerkleClaim.sol (L69-74)
```text
  function sweep(address to) public {
    require(msg.sender == TL_MULTISIG);
    // allow sweep after 60 days
    require(block.timestamp > deployTime + 60 days);
    address _token = token;
    IERC20Upgradeable(_token).transfer(to, IERC20Upgradeable(_token).balanceOf(address(this)));
```
