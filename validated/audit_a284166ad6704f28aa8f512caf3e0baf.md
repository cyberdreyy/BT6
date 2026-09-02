### Title
Stale confirmations from removed multisig members are never purged, allowing requests to execute below the live-member confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` treats the size of the `confirmations` set for a request as proof that `num_confirmations` distinct **current** members approved it. `delete_member` only cleans up confirmations for requests **authored** by the removed member; it never removes that member's confirmation entries from other requests that member had merely **confirmed**. A confirmation cast by a member who is later removed therefore stays counted forever, letting an attacker combine one stale confirmation with fewer live confirmations than `num_confirmations` to execute an arbitrary request (transfer, deploy, add key, etc.).

### Finding Description
`confirm()` only checks the cardinality of the confirmation set against `num_confirmations`, with no re-validation that every entry in that set still belongs to `self.members`: [1](#0-0) 

`delete_member()` is the only place that scrubs confirmation state when a member leaves, and it filters by `r.member == member`, i.e. it only clears requests **created** by the departing member — not the departing member's confirmations on requests created by someone else: [2](#0-1) 

The `MultiSigRequestWithSigner` struct only records the single author of a request, not the set of confirmers, so there is no code path capable of purging a removed member's confirmation from requests it did not author: [3](#0-2) [4](#0-3) 

This breaks the intended invariant `confirmations_counted == live_members_who_confirmed`. Once broken, `confirmations.len() >= num_confirmations` can be satisfied by a mix of live and stale (former-member) confirmations, so a request executes with strictly fewer than `num_confirmations` currently-authorized signers.

### Impact Explanation
This is a Critical-severity issue per the rules ("a multisig request executed below threshold"). A k-of-n multisig protecting a lockup owner, a foundation account, or any custody account can have a `Transfer`, `DeployContract`, `AddKey`, or `AddMember`/`DeleteMember` action executed with less than `num_confirmations` live, currently-trusted signers — e.g. an already-compromised or since-removed key's old confirmation can be "reused" indefinitely to help push a later, unrelated malicious request over threshold.

### Likelihood Explanation
No privileged setup is required beyond what any legitimate multisig already does in the course of normal operation (a member confirms a request, then some future request removes that member). The remaining attacker only needs to control (or bribe) one live member to add the final confirmation that lets the stale entry tip the count over the threshold — well within the "unprivileged attacker" bar since any member with normal confirm rights can trigger it, and it does not require any foundation, redeploy, or victim key.

### Recommendation
When counting confirmations in `confirm()`, filter the `confirmations` set to members currently present in `self.members` before comparing against `num_confirmations` (or scrub confirmations proactively: on `delete_member`, iterate all entries in `self.confirmations` and remove the deleted member's `to_string()` key from every request's confirmation set, not just requests it authored).

### Proof of Concept
Extending the existing test harness in `multisig2/src/lib.rs` (`num_confirmations = 2`, members `{A, B, C}`):

```rust
// members: A, B, C ; num_confirmations = 2
let mut c = MultiSigContract::new(vec![member_a(), member_b(), member_c()], 2);

// 1. A creates a Transfer request R (not auto-confirmed).
testing_env!(context_for(a()));
let r_id = c.add_request(transfer_request());

// 2. B confirms R -> confirmations(R) = {B}; count = 1 < 2, not executed.
testing_env!(context_for(b()));
c.confirm(r_id);

// 3. A creates+confirms a DeleteMember(B) request, C confirms -> reaches
//    num_confirmations = 2 and executes: B is removed from `members`.
//    delete_member() only clears confirmations for requests authored by B,
//    so confirmations(R) still contains "B".
testing_env!(context_for(a()));
let del_id = c.add_request_and_confirm(delete_member_request(b()));
testing_env!(context_for(c()));
c.confirm(del_id); // executes DeleteMember(B); members now = {A, C}

// 4. C confirms R -> confirmations(R) = {B, C}; count = 2 >= num_confirmations,
//    so R executes even though only C is a genuinely live confirming member.
testing_env!(context_for(c()));
c.confirm(r_id); // Transfer executes with only 1 live confirmation, not 2.
```

The transfer in `R` is dispatched despite the multisig having only one live, currently-authorized confirmer (`C`) instead of the required two, because the removed member `B`'s stale confirmation is never purged.

### Citations

**File:** multisig2/src/lib.rs (L85-92)
```rust
#[derive(BorshDeserialize, BorshSerialize, Serialize, Deserialize)]
#[cfg_attr(test, derive(PartialEq, Clone))]
#[serde(crate = "near_sdk::serde")]
pub struct MultiSigRequestWithSigner {
    request: MultiSigRequest,
    member: MultisigMember,
    added_timestamp: u64,
}
```

**File:** multisig2/src/lib.rs (L126-128)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
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
