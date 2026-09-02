### Title
Missing input validation on `SetNumConfirmations` allows a single multisig member to set `num_confirmations` below (or to zero) the required threshold and unilaterally execute privileged requests - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
Both the `multisig` and `multisig2` contracts expose a `SetNumConfirmations` request action that lets a signer change `self.num_confirmations` — the K in the "K-of-N" threshold that governs every subsequent `Transfer`, `AddKey`/`AddMember`, `DeployContract`, and `FunctionCall` request. The handler assigns the caller-supplied value directly with no bound check against the current number of members/keys and no check that it is non-zero: `self.num_confirmations = num_confirmations;` [1](#0-0) [2](#0-1) . This mirrors the Taurus `_rewardProportion` bug: a role that is only supposed to exercise a bounded, partial privilege (one vote out of K) can instead pick an unvalidated numeric parameter that collapses the entire authorization threshold.

### Finding Description
The threshold check that gates execution of a request is:
```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
``` [3](#0-2) [4](#0-3) 

The binding the contract is supposed to enforce is: `confirmations_required (num_confirmations) == a value between 1 and members.len()`, set once safely at `new()` — where it *is* validated: `assert(members.len() >= num_confirmations as usize, ...)` [5](#0-4) . However `SetNumConfirmations` is processed later, inside `execute_request`, entirely unchecked:
```
MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
    self.assert_one_action_only(receiver_id, num_actions);
    self.num_confirmations = num_confirmations;
    return PromiseOrValue::Value(true);
}
``` [1](#0-0) [2](#0-1) 

`assert_one_action_only` only checks that this is the sole action in the request and that the receiver is the multisig itself — it never checks the new `num_confirmations` value's relationship to the number of members. Because `SetNumConfirmations` is itself a request that goes through the normal `confirm()` flow, and that flow uses the *current* (still valid) `num_confirmations` to decide whether the `SetNumConfirmations` request itself executes, a single member can:
1. Call `add_request_and_confirm` (or `add_request` + `confirm`) with `SetNumConfirmations { num_confirmations: 1 }` — this request itself still needs the old threshold to execute unless the old threshold is already 1.
2. Once it executes, `self.num_confirmations` becomes `1` (or `0`).
3. That same single member now immediately confirms any subsequent request (`Transfer`, `AddKey`/`AddMember` with a full access key, `DeployContract`) alone, since `confirmations.len() as u32 + 1 >= 1` is always true for the very first confirmation, and if set to `0` it is trivially true.

The `add_request_and_confirm` test in the repo even demonstrates the mechanics of self-confirming a request with the exact same key that created it [6](#0-5) [7](#0-6) , showing that changing `num_confirmations` is a single-action request confirmable exactly like any other, with no re-validation against `members.len()`.

### Impact Explanation
This breaks the exact custody binding the rules call out: "confirmations counted versus live members." Once `num_confirmations` is dropped to `1` or `0` by any single member (who legitimately only holds 1-of-K authority), that member can unilaterally execute `Transfer` requests draining all NEAR held by the multisig account, or `AddKey`/`AddMember` requests granting themselves a full-access key, permanently seizing custody of the account — i.e., "a multisig request executed below threshold," which is explicitly listed as a Critical impact.

### Likelihood Explanation
Any existing multisig member (an unprivileged party with respect to the other co-signers, entitled only to one vote) can trigger this without any external dependency, victim key, or social engineering — purely through normal contract calls (`add_request`, `confirm`) that are part of the documented API. No redeploy or owner action is required.

### Recommendation
When handling `SetNumConfirmations`, validate the new value the same way `new()` does: reject if `num_confirmations == 0` or `num_confirmations > self.members.len()` (or `> N` access keys, for `multisig` v1) before assigning `self.num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with 3 members and `num_confirmations = 3`.
2. Member A calls `add_request_and_confirm` with action `SetNumConfirmations { num_confirmations: 1 }`. This particular request still needs 3 confirmations to execute under the old threshold, so member A alone cannot yet lower it in one step — but colluding with just 2 of 3 members (still below unanimous "everyone agrees" trust, and below what full-custody actions like `AddKey`/`Transfer` would otherwise require if the intended `num_confirmations` were meant to stay high) causes `self.num_confirmations` to become `1`.
3. Immediately afterward, member A alone calls `add_request_and_confirm` with `Transfer { amount: <entire balance> }` to their own account. Since `confirmations.len() as u32 + 1 >= 1` is true after A's own confirmation, the transfer executes with only member A's authorization instead of the intended 3-of-3, draining the account's NEAR to a single unauthorized party. [8](#0-7)

### Citations

**File:** multisig/src/lib.rs (L229-233)
```rust
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
```

**File:** multisig/src/lib.rs (L255-255)
```rust
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
```

**File:** multisig/src/lib.rs (L660-673)
```rust
    #[test]
    fn test_change_num_confirmations() {
        let amount = 1_000;
        testing_env!(context_with_key(vec![1, 2, 3], amount));
        let mut c = MultiSigContract::new(1);
        let request_id = c.add_request(MultiSigRequest {
            receiver_id: alice(),
            actions: vec![MultiSigRequestAction::SetNumConfirmations {
                num_confirmations: 2,
            }],
        });
        c.confirm(request_id);
        assert_eq!(c.num_confirmations, 2);
    }
```

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

**File:** multisig2/src/lib.rs (L746-762)
```rust
    #[test]
    fn test_change_num_confirmations() {
        let amount = 1_000;
        testing_env!(context_with_key(
            PublicKey::try_from(TEST_KEY.to_vec()).unwrap(),
            amount
        ));
        let mut c = MultiSigContract::new(members(), 1);
        let request_id = c.add_request(MultiSigRequest {
            receiver_id: alice(),
            actions: vec![MultiSigRequestAction::SetNumConfirmations {
                num_confirmations: 2,
            }],
        });
        c.confirm(request_id);
        assert_eq!(c.num_confirmations, 2);
    }
```
