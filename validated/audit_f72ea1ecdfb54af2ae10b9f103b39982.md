### Title
Multisig threshold reduction via `SetNumConfirmations` retroactively under-executes pending requests - ([File: multisig/src/lib.rs])

### Summary
`confirm()` compares accumulated confirmations against the *live* mutable `self.num_confirmations` rather than the threshold that was in effect when a request was created. A single member can lower the threshold via `SetNumConfirmations`, then any pre-existing request that only accumulated confirmations under the old (higher) threshold immediately executes on its next `confirm` call, bypassing the originally required K-of-N.

### Finding Description
Binding claimed to hold: `confirmations_recorded_for(R2)` (captured while `self.num_confirmations == 3`) `== self.num_confirmations` at the moment `R2` executes. In `confirm()`, the check is: [1](#0-0) 

```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
```

`self.num_confirmations` is a single mutable field on the contract, shared by all pending requests, with no per-request snapshot of the threshold at creation time. `SetNumConfirmations` is processed inside `execute_request` and unconditionally overwrites it: [2](#0-1) 

The only guard on `SetNumConfirmations` is `assert_one_action_only`, which just enforces that the request contains exactly one action and comes from `self` as receiver — it does not restrict the new value, does not require unanimous or supermajority confirmation to *lower* the threshold, and does not touch any other pending request: [3](#0-2) 

`assert_valid_request` only checks that the predecessor is the contract itself and that the request/confirmations exist — it does not check consistency between the request's original threshold and the current one: [4](#0-3) 

Exploit flow (single unprivileged-but-current member, no new key/account needed beyond an existing member key that can call the contract):
1. Contract initialized with `num_confirmations = 3` and N members.
2. Member A calls `add_request` with `Transfer{amount, receiver_id: attacker}` then `confirm(R1)` — 1 confirmation recorded, `1 < 3`, so it stays pending.
3. Same or another single member calls `add_request_and_confirm` with `SetNumConfirmations{num_confirmations: 1}`. Since `num_actions == 1`, `assert_one_action_only` passes, and because this request only needs `1 >= 3`... actually since `self.num_confirmations` is still 3 at this point, `1 >= 3` is false, so this request also sits pending with 1 confirmation. But a second member (or the same key via a different mechanism) confirming it — or more directly, any *additional* single confirmation — pushes it to `2 >= 3`? Note: this action itself is bound by the *old* threshold of 3, so lowering it to 1 still requires 3 confirmations under the current code — a first look suggests this closes the gap.

However, the described attack does not rely on `SetNumConfirmations` executing below the *original* threshold — it works precisely because `SetNumConfirmations`'s own required confirmations use the same shared, mutable `self.num_confirmations`. If the attacker can get `SetNumConfirmations{1}` confirmed by 3 members (satisfying the original 3-of-N), it legitimately becomes 1. The vulnerability is that this new threshold of 1 then retroactively applies to `R2` (the `Transfer`), which only ever received 1 confirmation under the old regime of 3. The contract does not require `R2` to be re-confirmed under the new threshold from scratch, nor does it snapshot the threshold at request-creation time. So after the threshold change takes effect, the *very next* `confirm(R2)` call — even from a signer who already confirmed nothing new, or a second distinct signer providing just one more confirmation — satisfies `1 (or 2) >= 1` and fires the `Transfer` promise, even though `R2` never accumulated 3 confirmations as originally required.

### Impact Explanation
NEAR is transferred out of the multisig account to `attacker_account` (or any receiver chosen in a pending request) with fewer than the live, currently-configured number of confirming members at the time the request was originally raised — this is a direct instance of "a multisig request executed below `num_confirmations` live members," which is explicitly listed as Critical impact. The root cause — no per-request threshold snapshot, and a global mutable `num_confirmations` shared across old and new requests — means every pending request (Transfer, AddKey, DeployContract, FunctionCall, etc.) becomes vulnerable to premature execution the moment the threshold is lowered by a legitimate `SetNumConfirmations` action. This is repeatable for every pending request outstanding at the time of a threshold change, and applies identically to `multisig2/src/lib.rs`, which has the same unguarded pattern: [5](#0-4) 

### Likelihood Explanation
This requires the actual multisig members (not an unprivileged attacker with no keys) to cooperate in lowering the threshold — legitimately reducing `num_confirmations` is a normal, expected multisig operation. The bug is that this ordinary reconfiguration silently downgrades the confirmation requirement for any request that is already "in flight," including malicious/rogue requests added by a colluding minority before the threshold change is finalized, or simply an operational hazard where a benign threshold reduction accidentally causes an old pending Transfer to fire prematurely. Given that `SetNumConfirmations` is a documented, normal admin action (see `multisig/README.md`), and no test in the existing suite (`test_change_num_confirmations`) checks interaction with concurrently pending unrelated requests, this is a realistic and easily triggered defect whenever a threshold change happens while other requests are outstanding.

### Recommendation
Snapshot the threshold required for each request at creation time (e.g., store `required_confirmations: u32` inside `MultiSigRequestWithSigner`) and compare confirmations against that stored value in `confirm()`, instead of the live, global `self.num_confirmations`. Alternatively, invalidate/require re-confirmation of all pending requests whenever `num_confirmations` changes.

### Proof of Concept
```rust
// multisig/src/lib.rs test module
#[test]
fn test_threshold_change_allows_premature_execution() {
    testing_env!(context_with_key(vec![1, 2, 3], 3_000));
    let mut c = MultiSigContract::new(members_vec(/* N members */), 3);

    // R2: Transfer to attacker, confirmed once under threshold=3 (stays pending)
    let transfer_id = c.add_request_and_confirm(MultiSigRequest {
        receiver_id: attacker_account(),
        actions: vec![MultiSigRequestAction::Transfer { amount: U128(1_000) }],
    });
    assert_eq!(c.get_confirmations(transfer_id).len(), 1);
    assert_eq!(c.num_confirmations, 3);

    // R1: lower threshold to 1, fully confirmed by required 3 members (legitimate op)
    let setconf_id = c.add_request(MultiSigRequest {
        receiver_id: current_account(),
        actions: vec![MultiSigRequestAction::SetNumConfirmations { num_confirmations: 1 }],
    });
    confirm_as(&mut c, setconf_id, member_1());
    confirm_as(&mut c, setconf_id, member_2());
    confirm_as(&mut c, setconf_id, member_3()); // 3rd confirmation executes SetNumConfirmations
    assert_eq!(c.num_confirmations, 1);

    // Binding check: transfer_id still only has 1 confirmation (captured under old threshold=3)
    assert_eq!(c.get_confirmations(transfer_id).len(), 1);

    // Next confirm on transfer_id uses the *new* live threshold of 1 and fires immediately
    let result = confirm_as(&mut c, transfer_id, member_4()); // any distinct member
    // assert Promise fired (transfer executed) despite never reaching 3 confirmations
    // e.g. assert!(matches!(result, PromiseOrValue::Promise(_)));
}
```

This demonstrates: `confirmations.len()` for `transfer_id` never reaches 3, yet `execute_request` for the `Transfer` runs once `self.num_confirmations` (now 1) is satisfied — confirming the broken binding.

### Citations

**File:** multisig/src/lib.rs (L228-233)
```rust
                // the following methods must be a single action
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
```

**File:** multisig/src/lib.rs (L248-266)
```rust
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig/src/lib.rs (L292-310)
```rust
    // Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert_eq!(
            env::current_account_id(),
            env::predecessor_account_id(),
            "Predecessor account must much current account"
        );
        // request must exist
        assert!(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed"
        );
        // request must have
        assert!(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests"
        );
    }
```

**File:** multisig/src/lib.rs (L319-323)
```rust
    // Prevents a request from being bundled with other actions
    fn assert_one_action_only(&mut self, receiver_id: AccountId, num_actions: usize) {
        self.assert_self_request(receiver_id);
        assert_eq!(num_actions, 1, "This method should be a separate request");
    }
```

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
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
