### Title
`SetNumConfirmations` action lacks the members-vs-threshold invariant enforced elsewhere, allowing `num_confirmations` to exceed live member count or be reduced to zero - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::new` enforces `members.len() >= num_confirmations` at construction, and `delete_member` re-checks `self.members.len() - 1 >= self.num_confirmations` before removing a member. However, the `MultiSigRequestAction::SetNumConfirmations` branch of `execute_request` sets `self.num_confirmations = num_confirmations` with no validation at all against `self.members.len()`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The contract maintains an invariant that `num_confirmations` (the threshold `k`) must never exceed the number of live members `n`, since `confirm()` executes a request once `confirmations.len() + 1 >= self.num_confirmations`. This invariant is validated in two places:
- Constructor `new()`: `assert(members.len() >= num_confirmations as usize, ...)`. [1](#0-0) 
- `delete_member()`: `assert(self.members.len() - 1 >= self.num_confirmations as u64, "Removing given member will make total number of members below number of confirmations")`. [2](#0-1) 

But the only other place `num_confirmations` can be mutated after initialization, `SetNumConfirmations`, performs no such check:
```
MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
    self.assert_one_action_only(receiver_id, num_actions);
    self.num_confirmations = num_confirmations;
    return PromiseOrValue::Value(true);
}
``` [3](#0-2) 

`assert_one_action_only` / `assert_self_request` only verify the request targets the contract itself and is not bundled with other actions — they do not check `num_confirmations` against `self.members.len()`. [4](#0-3) 

This breaks the equality the constructor and `delete_member` are meant to preserve: `num_confirmations ≤ members.len()`. Once this request is executed (via the normal `add_request`/`confirm` flow, requiring only the currently-valid threshold to pass — not requiring the attacker to be more privileged than any existing member), the contract can end up with:
- `num_confirmations = 0`, meaning `confirmations.len() as u32 + 1 >= self.num_confirmations` in `confirm()` is trivially true, so a single confirming member alone always executes any subsequent request — collapsing the multisig threshold to 1-of-n regardless of the configured `k`. [5](#0-4) 
- `num_confirmations > members.len()`, meaning no achievable set of confirmations can ever satisfy the threshold, permanently freezing every future request (including recovery requests to fix the threshold or add members) since those, too, require confirmations to reach the now-unreachable count.

### Impact Explanation
- Setting `num_confirmations` below the intended threshold (e.g., to `0` or `1`) allows a request to be executed with fewer confirmations than the multisig's documented/expected policy, i.e. **a multisig request executed below threshold**.
- Setting `num_confirmations` above `members.len()` makes the threshold permanently unreachable, since no request (including one to lower `num_confirmations` again) can gather enough confirmations — this is **funds permanently frozen** in the account controlled by the multisig, because the multisig account itself typically has no other access keys after `add_member`/`delete_member` remove full-access keys in favor of multisig-managed access keys.

### Likelihood Explanation
Exploiting this does not require exceeding normal multisig privileges: any request (including `SetNumConfirmations`) is submitted via `add_request` and executed once the *current* `num_confirmations` threshold of confirmations is reached — the same process used for every other legitimate multisig operation. Because the check that every other mutator of `num_confirmations` (`new`, `delete_member`) enforces is simply missing here, a single overlooked/careless `SetNumConfirmations` request (whether malicious by a subset of colluding-but-otherwise-authorized signers, or simply a misconfiguration) is sufficient to desynchronize `num_confirmations` from `members.len()`, with no additional guard preventing it.

### Recommendation
Add the same invariant check inside the `SetNumConfirmations` branch of `execute_request` (and ideally centralize it in one helper used by `new`, `delete_member`, and `SetNumConfirmations`):
```rust
MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
    self.assert_one_action_only(receiver_id, num_actions);
    assert(
        num_confirmations as u64 > 0 && num_confirmations as u64 <= self.members.len(),
        "num_confirmations must be positive and not exceed current number of members",
    );
    self.num_confirmations = num_confirmations;
    return PromiseOrValue::Value(true);
}
```

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]`, `num_confirmations = 2` (valid per constructor check). [1](#0-0) 
2. Member A calls `add_request` with a `SetNumConfirmations { num_confirmations: 0 }` action (a single, self-targeted action, satisfying `assert_one_action_only`). [6](#0-5) 
3. Member B calls `confirm(request_id)`. Since the *current* `num_confirmations` is still `2`, this second confirmation (1 existing + 1 new = 2 ≥ 2) triggers execution, and `self.num_confirmations` is set to `0` with no bound check. [3](#0-2) 
4. From this point on, in `confirm()`, `confirmations.len() as u32 + 1 >= self.num_confirmations` (`0 + 1 >= 0`) is always true, so any single member (A, B, or C) can add and immediately confirm-and-execute arbitrary requests — including `Transfer` actions moving all NEAR out of the multisig account — with zero actual multisig protection despite the contract's documented 2-of-3 threshold. [5](#0-4)

### Citations

**File:** multisig2/src/lib.rs (L148-152)
```rust
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
        );
```

**File:** multisig2/src/lib.rs (L274-279)
```rust
                // the following methods must be a single action
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

**File:** multisig2/src/lib.rs (L425-437)
```rust
    /// Prevents request from approving tx on another account
    fn assert_self_request(&mut self, receiver_id: AccountId) {
        assert(
            receiver_id == env::current_account_id(),
            "This method only works when receiver_id is equal to current_account_id",
        );
    }

    /// Prevents a request from being bundled with other actions
    fn assert_one_action_only(&mut self, receiver_id: AccountId, num_actions: usize) {
        self.assert_self_request(receiver_id);
        assert(num_actions == 1, "This method should be a separate request");
    }
```
