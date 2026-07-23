### Title
Last ADMIN can permanently renounce `ADMIN_ROLE` via inherited `renounceRole`, locking ETH and freezing critical oracle management — (`smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

`OracleBase` (providers) inherits OpenZeppelin `AccessControl` and bootstraps with `_setRoleAdmin(ADMIN_ROLE, ADMIN_ROLE)`, making `ADMIN_ROLE` self-administering. OpenZeppelin's `AccessControl` exposes an unconditional `renounceRole(role, callerConfirmation)` that any role-holder may call on themselves. If the last `ADMIN_ROLE` holder calls `renounceRole(ADMIN_ROLE, self)`, the contract is permanently left with zero admins and no recovery path, because `ADMIN_ROLE` is its own admin and `DEFAULT_ADMIN_ROLE` is never granted to anyone. Every `onlyRole(ADMIN_ROLE)` function — including `withdrawEth()` — becomes permanently inaccessible.

---

### Finding Description

**Root cause — constructor:**

```solidity
// OracleBase (providers), lines 50-51
_grantRole(ADMIN_ROLE, _owner);
_setRoleAdmin(ADMIN_ROLE, ADMIN_ROLE);   // ADMIN_ROLE is its own admin
``` [1](#0-0) 

`DEFAULT_ADMIN_ROLE` (`bytes32(0)`) is never granted to anyone. `ADMIN_ROLE` is the sole admin of itself. OpenZeppelin `AccessControl.renounceRole` is:

```solidity
function renounceRole(bytes32 role, address callerConfirmation) public virtual {
    if (callerConfirmation != _msgSender()) revert AccessControlBadConfirmation();
    _revokeRole(role, callerConfirmation);   // no "last-admin" guard
}
```

There is no check that at least one `ADMIN_ROLE` holder remains after the call. Once the last admin self-revokes, no address can call `grantRole(ADMIN_ROLE, ...)` because that path also requires the caller to hold `ADMIN_ROLE`.

**Affected ADMIN-gated functions in OracleBase (providers):**

| Function | Impact if ADMIN lost |
|---|---|
| `withdrawEth()` | All ETH (registration fees) permanently stuck |
| `addApprovedFactory()` / `removeApprovedFactory()` | Factory allow-list frozen; no new pools can register |
| `setBlacklist()` | Blacklisted pools can never be cleared |
| `addIntegrator()` / `removeIntegrator()` / `setIntegrators()` | Integrator whitelist permanently frozen |
| `setRegistrationFee()` | Fee permanently frozen |
| `setPriceGuard()` / `setStateGuardRole()` (when no explicit stateGuard) | Feed guards permanently frozen | [2](#0-1) [3](#0-2) 

The same pattern is replicated in `AnchoredProviderFactory`:

```solidity
// AnchoredProviderFactory.sol, lines 51-52
_grantRole(ADMIN_ROLE, _admin);
_setRoleAdmin(ADMIN_ROLE, ADMIN_ROLE);
``` [4](#0-3) 

If the last admin of `AnchoredProviderFactory` renounces, `addOracle`, `removeOracle`, `setEnvelope`, and `setFeedClass` are all permanently inaccessible, freezing the oracle allow-list and envelope system that gates all new `AnchoredPriceProvider` deployments.

---

### Impact Explanation

**Direct loss of protocol fees:** ETH accumulates in `OracleBase` via the permissionless `register()` call (each pool pays `registrationFee`). `withdrawEth()` sweeps the full balance and is gated exclusively on `ADMIN_ROLE`. After the last admin renounces, all accumulated ETH is permanently locked with no recovery path. [5](#0-4) 

**Broken core pool functionality:** `addApprovedFactory` is the only way to whitelist a factory so that pools can call `register()` and subsequently use the `price(feedId, pool)` read path. With no ADMIN, no new factory can ever be approved, and no new pool can ever register — permanently breaking the on-chain price read path for any pool deployed after the admin is lost. [6](#0-5) 

---

### Likelihood Explanation

Likelihood is **Low-Medium**. The trigger requires the last `ADMIN_ROLE` holder to call `renounceRole`. This can happen:
- Accidentally during a key-rotation sequence (revoke before grant)
- Via a compromised multisig that executes a malicious proposal
- Intentionally by a malicious insider

The action is irreversible with zero on-chain recovery. The design provides no guard (no minimum-admin check, no timelock, no two-step transfer) to prevent it.

---

### Recommendation

1. **Override `renounceRole`** in `OracleBase` (providers) and `AnchoredProviderFactory` to revert when the caller is the last `ADMIN_ROLE` holder:

```solidity
function renounceRole(bytes32 role, address callerConfirmation) public override {
    if (role == ADMIN_ROLE && getRoleMemberCount(ADMIN_ROLE) == 1) {
        revert CannotRenounceLastAdmin();
    }
    super.renounceRole(role, callerConfirmation);
}
```

2. **Override `revokeRole`** with the same guard so an admin cannot remove the last peer admin either.

3. Alternatively, adopt a two-step ownership transfer pattern (propose → accept) before any revocation takes effect, ensuring a successor is always confirmed first.

---

### Proof of Concept

```
1. Deploy OracleBase with _owner = alice (alice is the sole ADMIN_ROLE holder).
2. Pools call register() over time; ETH accumulates in the contract.
3. Alice calls: renounceRole(ADMIN_ROLE, alice)
   → _revokeRole removes alice; getRoleMemberCount(ADMIN_ROLE) == 0.
4. Alice (or anyone) calls withdrawEth()
   → reverts: AccessControlUnauthorizedAccount (no ADMIN_ROLE holder exists).
5. Alice calls addApprovedFactory(newFactory)
   → reverts: same reason.
6. All ETH is permanently locked; no new pool can ever register for price reads.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L49-54)
```text
    constructor(address _owner, uint256 maxTimeDrift) {
        _grantRole(ADMIN_ROLE, _owner);
        _setRoleAdmin(ADMIN_ROLE, ADMIN_ROLE);
        MAX_TIME_DRIFT = maxTimeDrift;
        registrationFee = 1 wei; // very cheap default; ADMIN tunes via setRegistrationFee
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L201-213)
```text
    function register(bytes32 feedId, address pool, address factory) external payable {
        require(msg.value >= registrationFee, InsufficientFee(msg.value, registrationFee));
        require(pool != address(0));
        require(approvedFactories.contains(factory), FactoryNotApproved(factory));
        require(IPoolFactory(factory).isPool(pool), NotAPool(pool));

        if (blacklisted[pool]) {
            blacklisted[pool] = false;
            emit BlacklistUpdated(pool, false);
        }

        registeredPool[feedId][pool] = true;
        emit PoolRegistered(feedId, pool, msg.sender, msg.value);
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L260-270)
```text
    function addApprovedFactory(address factory) external onlyRole(ADMIN_ROLE) {
        require(factory != address(0));
        require(approvedFactories.add(factory), FactoryAlreadyApproved(factory));
        emit ApprovedFactoryAdded(factory);
    }

    function removeApprovedFactory(address factory) external onlyRole(ADMIN_ROLE) {
        require(approvedFactories.remove(factory), FactoryNotApproved(factory));
        emit ApprovedFactoryRemoved(factory);
    }

```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L292-297)
```text
    function withdrawEth() external onlyRole(ADMIN_ROLE) {
        uint256 amount = address(this).balance;
        (bool ok, ) = payable(msg.sender).call{value: amount}("");
        require(ok);
        emit EthWithdrawn(msg.sender, amount);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L49-53)
```text
    constructor(address _admin) {
        // No oracle is seeded here — the allow-list starts empty and is populated via addOracle (admin).
        _grantRole(ADMIN_ROLE, _admin);
        _setRoleAdmin(ADMIN_ROLE, ADMIN_ROLE);
    }
```
