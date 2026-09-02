### Title
`MultiSigContract::execute_request`'s `SetNumConfirmations` action does not re-validate the `num_confirmations <= members.len()` invariant, permanently freezing multisig funds - (File: `multisig2/src/lib.rs`)

### Summary
`multisig2`'s constructor enforces `members.len() >= num_confirmations` at init time, and `delete_member` re-checks that invariant before removing a member. But the `SetNumConfirmations` request action - reachable through the exact same `execute_request` quorum pipeline - writes `self.num_confirmations` directly with no bound check against the current member count, so an approved request can set `num_confirmations` above `members.len()`, making the required quorum permanently unreachable.

### Finding Description
The custody binding that must hold for the multisig to remain operable is:

```
num_confirmations <= members.len()
```

This is checked in two places:
- `new()`: `assert(members.len() >= num_confirmations as usize, "Members list must be equal or larger than number of confirmations")` [1](#0-0) 
- `delete_member()`: `assert(self.members.len() - 1 >= self.num_confirmations as u64, "Removing given member will make total number of members below number of confirmations")` [2](#0-1) 

However, the `SetNumConfirmations` arm of `execute_request` - the only other place `num_confirmations` can change post-init - performs no such check:

```rust
MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
    self.assert_one_action_only(receiver_id, num_actions);
    self.num_confirmations = num_confirmations;
    return PromiseOrValue::Value(true);
}
``` [3](#0-2) 

`assert_one_action_only` only checks that the receiver is the multisig itself and that the request contains a single action - it never inspects `self.members.len()`:

```rust
fn assert_one_action_only(&mut self, receiver_id: AccountId, num_actions: usize) {
    self.assert_self_request(receiver_id);
    assert(num_actions == 1, "This method should be a separate request");
}
``` [4](#0-3) 

This is the same bug class as the external report: a validity invariant enforced by a guard at one code path (constructor / `delete_member`) but silently unenforced on an equally-reachable state-mutating path (`SetNumConfirmations`) that shares the same `execute_request` dispatch. The existing `#[test] fn test_too_many_confirmations` even demonstrates the constructor guard catches this at init time, but no equivalent test or guard exists for `SetNumConfirmations`: [5](#0-4) 

### Impact Explanation
Once `num_confirmations` exceeds `members.len()`, `confirm()`'s quorum check `confirmations.len() as u32 + 1 >= self.num_confirmations` can never be satisfied by any real set of members [6](#0-5) . Since `Transfer`, `AddMember`, `DeleteMember`, `AddKey`, and `FunctionCall` - i.e., every path capable of moving funds or restoring a sane `num_confirmations` - all require reaching that same unreachable quorum, the account and any NEAR balance it holds become permanently frozen with no on-chain recovery path. This satisfies the Critical impact criterion "funds permanently frozen."

### Likelihood Explanation
`SetNumConfirmations` is a normal, always-available multisig request type requiring only the contract's existing configured quorum to approve - the same threshold used for every other operation, including benign administrative changes. A miscalibrated or malicious `SetNumConfirmations` request (e.g., set to a value greater than the current or soon-to-be-reduced member count) is a single, unremarkable-looking proposal that any subset of members reaching the current threshold could pass, mirroring exactly how the original governance report requires a normal proposal to pass to brick the system.

### Recommendation
Extend the `SetNumConfirmations` arm of `execute_request` to reuse the same invariant check already used in `new()` and `delete_member`:

```rust
MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
    self.assert_one_action_only(receiver_id, num_actions);
    assert(
        self.members.len() >= num_confirmations as u64,
        "Number of confirmations must not exceed current number of members",
    );
    self.num_confirmations = num_confirmations;
    return PromiseOrValue::Value(true);
}
```

### Proof of Concept
Using the existing test harness in `multisig2/src/lib.rs`:
1. Deploy with `MultiSigContract::new(members(), 1)` where `members().len() == 4`.
2. Submit and confirm a request `SetNumConfirmations { num_confirmations: 10 }` (only 1 confirmation is needed per this contract's threshold) - it succeeds because `execute_request`'s `SetNumConfirmations` arm performs no bound check [3](#0-2) .
3. Attempt any subsequent `Transfer`, `AddMember`, or another `SetNumConfirmations` request and call `confirm()` from every one of the 4 members - `confirmations.len() as u32 + 1 >= self.num_confirmations` (i.e. `>= 10`) can never be true, so the request can never execute. The multisig account is permanently frozen.

### Citations

**File:** multisig2/src/lib.rs (L148-152)
```rust
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
        );
```

**File:** multisig2/src/lib.rs (L275-279)
```rust
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
```

**File:** multisig2/src/lib.rs (L294-315)
```rust
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L356-360)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
```

**File:** multisig2/src/lib.rs (L433-437)
```rust
    /// Prevents a request from being bundled with other actions
    fn assert_one_action_only(&mut self, receiver_id: AccountId, num_actions: usize) {
        self.assert_self_request(receiver_id);
        assert(num_actions == 1, "This method should be a separate request");
    }
```

**File:** multisig2/src/lib.rs (L869-877)
```rust
    #[test]
    #[should_panic]
    fn test_too_many_confirmations() {
        testing_env!(context_with_key(
            PublicKey::try_from(TEST_KEY.to_vec()).unwrap(),
            1_000
        ));
        let _ = MultiSigContract::new(members(), 5);
    }
```
