Confirmed. The vulnerability I identified in `multisig2/src/lib.rs` is real and precisely matches the analog bug class: a stale piece of state (a confirmation recorded under a member's identity) continues to be trusted/counted after the binding that justified it (live membership) has been broken, exactly like the Merkl disputer flag being ignored after being set.

### Title
Stale confirmations from removed multisig members still count toward execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` removes a member from `self.members` but only cleans up confirmations for requests that member itself *originated* (`r.member == member`). It never scrubs that member's `to_string()` entry out of the `confirmations` `HashSet<String>` of *other* active requests (originated by different members) that this member had already confirmed. `confirm()` later counts `confirmations.len()` against `num_confirmations` without checking that every entry in the set still corresponds to a live member, so a stale confirmation from a removed member is counted as if it were a real, currently-authorized signer.

### Finding Description
`confirm()` looks up the confirmation set for a request and checks the threshold purely by cardinality: [1](#0-0) 
Nothing in this function re-validates that the strings stored in `confirmations` still map to current `members`.

`delete_member()` only purges requests/confirmations keyed by the *removed member's own* outstanding requests: [2](#0-1) 
It does not scan `self.confirmations` for entries containing `member.to_string()` on requests originated by *other* members. Those confirmations remain in place.

The binding that should hold is:
`num_confirmations threshold met` ⟺ `count of confirmations from accounts/keys that are members of self.members at time of threshold check`

After a member is removed, this becomes:
`confirmations.len() (includes 1 stale entry from removed member)` ≥ `num_confirmations`, while `live members who actually confirmed < num_confirmations` [3](#0-2) 
The `assert` here only guards that the *total member count* stays `>= num_confirmations`; it says nothing about whether outstanding confirmation sets on unrelated requests still contain that removed member's stale vote.

### Impact Explanation
This lets a coalition smaller than `num_confirmations` execute privileged `MultiSigRequestAction`s (including `Transfer`, `AddKey`, `AddMember`/`DeleteMember`, `FunctionCall`) on the multisig account. Concretely:
1. Member A creates/confirms request R1 (not yet at threshold).
2. Legitimate governance removes member A via `DeleteMember` (A may have voted for its own removal, or be removed for other reasons) — R1 is untouched because it wasn't originated by A.
3. Remaining members confirm R1. The stale confirmation from A is still counted, so R1 executes with fewer live confirmations than `num_confirmations` requires — e.g. a transfer of NEAR is authorized by `k-1` live members instead of `k`, or a malicious/former member's approval is used to push through a fund transfer after their access was supposed to be revoked.

This directly breaks the "confirmations counted versus live members" custody binding and results in a multisig request executed below the intended threshold, moving NEAR (or granting keys) without the required quorum of currently-authorized parties — a Critical-severity issue per the impact criteria (multisig request executed below threshold).

### Likelihood Explanation
This requires no attacker-controlled deployment parameters and no owner/foundation collusion beyond the normal, expected use of `DeleteMember`, which is a routine multisig operation (e.g., rotating keys, offboarding a member). Any request that received a confirmation from a member prior to that member's removal will silently retain a phantom vote. The bug is triggered by ordinary usage patterns, not an edge-case attack setup, making it highly likely to occur over the lifetime of a long-lived multisig with membership churn.

### Recommendation
When removing a member in `delete_member`, iterate all active `self.requests`/`self.confirmations` entries and strip `member.to_string()` from every confirmation set (not just requests originated by that member). Alternatively, validate at `confirm()`-time that every string in the confirmation set still corresponds to a current member (filtering stale entries) before comparing `confirmations.len()` against `self.num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [Alice, Bob, Carol]`, `num_confirmations = 2`.
2. Bob calls `add_request` for `MultiSigRequest { receiver_id: <this contract>, actions: [Transfer{...}] }` → `request_id = 1` (not auto-confirmed).
3. Alice calls `confirm(1)` → `confirmations[1] = {Alice}` (1 confirmation, below threshold of 2).
4. Separately, the group executes a `DeleteMember{ member: Alice }` request (e.g., Alice is leaving the org) via `add_request_and_confirm` + `confirm` from Bob/Carol → `delete_member` runs; because request 1 was created by Bob (not Alice), the loop `filter_map(|(k,r)| r.member == member)` does not touch request 1, so `confirmations[1]` still equals `{Alice}` even though Alice is no longer in `self.members`.
5. Carol calls `confirm(1)` → `confirmations.len() (1) + 1 = 2 >= num_confirmations (2)` → request executes, using Alice's stale confirmation even though Alice was removed and never re-confirmed after removal. Only 1 truly live member (Carol) plus the stale entry authorized a Transfer that should have needed 2 live confirmations.

### Citations

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

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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
