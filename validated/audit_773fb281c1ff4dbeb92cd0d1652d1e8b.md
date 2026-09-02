### Title
Confirmation from a deleted multisig member allows a request to execute below `num_confirmations` live members - ([File: multisig2/src/lib.rs])

### Summary
`delete_member` only purges pending requests whose `MultiSigRequest.member` (the *original requester*) equals the member being deleted; it does not scan `self.confirmations` for entries where the deleted member merely confirmed someone else's request. As a result, a stale confirmation string from an account that is no longer a member remains counted toward quorum in `confirm`, letting a request execute with fewer live confirming members than `num_confirmations`.

### Finding Description
The binding that must hold is: `count(m in confirmations.get(request_id) such that m in self.members) == num_confirmations` at the moment `execute_request` fires. The actual check in `confirm` (multisig2/src/lib.rs:294-315) is only `confirmations.len() as u32 + 1 >= self.num_confirmations`, i.e. it counts *string entries* in the stored `HashSet<String>`, never re-validating that each entry still corresponds to a current member of `self.members`.

`delete_member` (multisig2/src/lib.rs:356-379) removes pending requests only via:
```
let request_ids: Vec<u32> = self.requests.iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
```
This filters on `r.member`, the account that originally called `add_request` for that request (multisig2/src/lib.rs:190-194 sets `member: current_member` at creation). It does not inspect `self.confirmations` entries at all, so any request `R2` that the deleted member confirmed (but did not create) keeps that member's `to_string()` entry in `confirmations.get(R2)` untouched.

Exploit flow:
1. Member A calls `add_request` creating `R2` (e.g. a `Transfer`), stored with `member: A` and empty confirmations (multisig2/src/lib.rs:190-197).
2. Member B (victim) calls `confirm(R2)`; since quorum isn't reached, B's string is inserted into `confirmations.get(R2)` (multisig2/src/lib.rs:311-312).
3. Separately, a `DeleteMember{member: B}` request reaches quorum and executes `delete_member`, removing B from `self.members` and only purging requests where `r.member == B` — `R2` (whose `r.member == A`) is untouched, and B's stale confirmation string remains in `confirmations.get(R2)`.
4. Member C calls `confirm(R2)`. `confirm` checks `!confirmations.contains(C)` (true) and computes `confirmations.len() + 1 >= num_confirmations`, counting B's stale entry even though B is no longer in `self.members`. If `num_confirmations == 2`, this reaches quorum with only one live confirming member (C), since B is gone.
5. `execute_request(R2)` fires, e.g. transferring NEAR out of the multisig account, authorized by only 1 live member instead of 2.

None of the existing guards prevent this: `assert_valid_request` (multisig2/src/lib.rs:407-423) only checks that the *caller* (C) is a current member and that the request/confirmations exist — it does not revalidate the *existing* confirmation entries against current membership. `current_member()` (multisig2/src/lib.rs:322-339) is only used for the caller, not retroactively for past confirmers.

### Impact Explanation
This directly violates the "multisig request executed below `num_confirmations` live members" Critical criterion. NEAR (or any `MultiSigRequestAction`, including `Transfer`, `AddKey` with full access, `FunctionCall`, etc.) can be executed with fewer live confirming signers than the configured threshold, effectively lowering the multisig's security guarantee whenever any member who confirmed a still-pending request is later removed. This is repeatable for every pending request that has a confirmation from a member later deleted, and scales with the number of long-lived pending requests in any deployed `multisig2` instance.

### Likelihood Explanation
The precondition (a pending request confirmed by one member, followed by that member's removal via a separate, legitimate `DeleteMember` quorum, before the first request executes) is a plausible operational sequence for any multisig-managed account — member rotation is a normal admin action and does not require attacker collusion beyond being one of the remaining live members driving the final `confirm`. No unusual privilege is needed beyond normal multisig membership; the "attacker" here is effectively a remaining honest-looking member exploiting a stale confirmation, or a scenario engineered by a malicious member colluding with removal timing. The bug is deterministic and 100% reproducible given the described request/member sequence, with no need to guess timing beyond ordering two on-chain multisig actions.

### Recommendation
When executing/counting confirmations in `confirm`, filter `confirmations.get(&request_id)` to only entries where the string still identifies a current `self.members` entry before comparing against `num_confirmations`. Alternatively, `delete_member` should scan `self.confirmations` for all request ids containing the deleted member's string and remove that entry (or remove the whole request if removal would drop it below quorum eligibility), not just requests where the member was the original requester.

### Proof of Concept
```rust
#[test]
fn test_stale_confirmation_after_member_deletion_bypasses_quorum() {
    // Setup: multisig with members A, B, C, num_confirmations = 2
    let mut contract = MultiSigContract::new(vec![
        member_a.clone(), member_b.clone(), member_c.clone()
    ], 2);

    // Step 1: A adds request R2 (e.g. Transfer)
    testing_env!(context_as(A));
    let r2 = contract.add_request(transfer_request());

    // Step 2: B confirms R2 (quorum not reached: 1 < 2)
    testing_env!(context_as(B));
    contract.confirm(r2);
    assert!(contract.get_confirmations(r2).contains(&B.to_string()));

    // Step 3: DeleteMember{B} executes via separate request/quorum
    // (simulate reaching quorum among A, C for DeleteMember{B})
    delete_member_via_quorum(&mut contract, B.clone());
    assert!(!contract.get_members().contains(&B_member));
    // BUG: R2's confirmations still contain B's stale string
    assert!(contract.get_confirmations(r2).contains(&B.to_string()));

    // Step 4: C alone confirms R2 -> reaches len()+1 (B stale) + C == 2 >= num_confirmations
    testing_env!(context_as(C));
    let result = contract.confirm(r2);

    // Assertion of the violated binding:
    // live confirming members for R2 = {C} (B no longer in self.members) => count == 1
    // but execute_request(R2) fired because raw confirmations.len()+1 == 2 == num_confirmations
    assert_execute_request_fired(result); // Transfer promise scheduled
    // 1 (live confirmer) != num_confirmations (2) => binding violated, Critical impact demonstrated
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** multisig2/src/lib.rs (L190-207)
```rust
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
    }

    /// Add request for multisig and confirm with the pk that added.
    pub fn add_request_and_confirm(&mut self, request: MultiSigRequest) -> RequestId {
        let request_id = self.add_request(request);
        self.confirm(request_id);
        request_id
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

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```

**File:** multisig2/src/lib.rs (L407-423)
```rust
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```
