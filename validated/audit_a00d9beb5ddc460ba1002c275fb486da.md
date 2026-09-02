## Title
Multisig executes requests with confirmations counted from removed members, allowing execution below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending requests and confirmation records that were *originated* by the removed member, but never scrubs that member's confirmation entries from other still-active requests that the member had previously confirmed. Because `confirm()` counts confirmations purely by set size (`confirmations.len() as u32 + 1 >= self.num_confirmations`) without checking that every recorded confirmer is still a current member, a request can be executed even though the number of confirmations coming from *live* members is below `num_confirmations`.

### Finding Description
`delete_member` removes the member from `self.members` and cleans up only requests where `r.member == member` (i.e., requests the removed member itself created): [1](#0-0) 

It does not iterate `self.confirmations` to strip the removed member's string identity from confirmation sets of *other* pending requests that this member had confirmed before being removed.

`confirm()` then decides whether to execute purely from the raw cardinality of the stored confirmation set: [2](#0-1) 

`assert_valid_request` only checks that the *caller* confirming right now is a current member; it never re-validates the members already recorded in `confirmations`: [3](#0-2) 

Concretely: with `num_confirmations = 3` and members {A, B, C, D}:
1. A creates request R (transfer funds) and confirms it → `confirmations(R) = {A}`.
2. B confirms → `confirmations(R) = {A, B}` (2 confirmations, below threshold, request stays pending).
3. Members execute a separate multisig request removing B (`DeleteMember { member: B }`). `delete_member` only cleans up requests *created by* B, not R, whose confirmation set still contains B even though B is no longer a member.
4. Later, C (a live member) confirms R. `confirmations.len() as u32 + 1 = 3 >= 3` → the request executes.

At execution time the live confirming set is really `{A, C}` — only 2 live members — while B's stale confirmation is silently counted toward the threshold. The equality that should hold, `confirmations_counted == live_members_who_confirmed`, is broken: `confirmations_counted (3) > live_members_who_confirmed (2)`.

### Impact Explanation
This lets a multisig request (including a `Transfer` moving NEAR, or an `AddKey`/`AddMember` request) execute with fewer live, currently-trusted confirmers than `num_confirmations` requires. This is exactly the "multisig request executed below threshold" critical impact: NEAR can be transferred, or a new full-access key/member can be added, without the intended number of active signers actually approving at execution time.

### Likelihood Explanation
Likely in ordinary operation, not just adversarial: member turnover (e.g. rotating a compromised key, offboarding a team member) is a routine multisig lifecycle event. Any time a member is removed while they have outstanding confirmations on other pending requests, those stale confirmations remain counted forever (or until the request is separately deleted), silently lowering the effective threshold for that request without any of the current members realizing it.

### Recommendation
When a member is deleted (`delete_member`), iterate all active `requests`/`confirmations` entries and remove the deleted member's identity from every confirmation set (not just requests they authored), or alternatively have `confirm()` re-validate that every entry in the stored confirmation set still belongs to `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
Using `multisig2/src/lib.rs` test harness style (see existing tests around [4](#0-3) ):
1. `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. As A: `add_request_and_confirm(transfer_request)` → confirmations = `{A}`.
3. As B: `confirm(request_id)` → confirmations = `{A, B}`.
4. As any live member with sufficient confirmations, execute a `DeleteMember{member: B}` request to remove B from `self.members`.
5. As C: `confirm(request_id)` → `confirmations.len() + 1 == 3 == num_confirmations`, so `execute_request` runs the transfer — even though only A and C are still live members who actually confirmed at execution time.

### Citations

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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
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

**File:** multisig2/src/lib.rs (L573-612)
```rust
    #[test]
    fn test_multi_3_of_n() {
        let amount = 1_000;
        testing_env!(context_with_key(
            PublicKey::from(
                "Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy"
                    .parse()
                    .unwrap()
            ),
            amount
        ));
        let mut c = MultiSigContract::new(members(), 3);
        let request = MultiSigRequest {
            receiver_id: bob(),
            actions: vec![MultiSigRequestAction::Transfer {
                amount: amount.into(),
            }],
        };
        let request_id = c.add_request(request.clone());
        assert_eq!(c.get_request(request_id), request);
        assert_eq!(c.list_request_ids(), vec![request_id]);
        c.confirm(request_id);
        assert_eq!(c.requests.len(), 1);
        assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 1);
        testing_env!(context_with_key(
            PublicKey::from(
                "HghiythFFPjVXwc9BLNi8uqFmfQc1DWFrJQ4nE6ANo7R"
                    .parse()
                    .unwrap()
            ),
            amount
        ));
        c.confirm(request_id);
        assert_eq!(c.confirmations.get(&request_id).unwrap().len(), 2);
        assert_eq!(c.get_confirmations(request_id).len(), 2);
        testing_env!(context_with_account(bob(), amount));
        c.confirm(request_id);
        // TODO: confirm that funds were transferred out via promise.
        assert_eq!(c.requests.len(), 0);
    }
```
