import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
# todo: the path from https://github.com/gitlabhq/gitlabhq
SOURCE_REPO = "gitlabhq/gitlabhq"
# todo: the name of the repository
REPO_NAME = "gitlabhq"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    # =================================================================================
    # Declarative authorization: policies that decide every permission check
    # =================================================================================
    "app/policies/base_policy.rb",
    "app/policies/project_policy.rb",
    "app/policies/group_policy.rb",
    "app/policies/global_policy.rb",
    "app/policies/namespace_policy.rb",
    "app/policies/user_policy.rb",
    "app/policies/issuable_policy.rb",
    "app/policies/issue_policy.rb",
    "app/policies/merge_request_policy.rb",
    "app/policies/note_policy.rb",
    "app/policies/blob_policy.rb",
    "app/policies/commit_policy.rb",
    "app/policies/project_snippet_policy.rb",
    "app/policies/personal_snippet_policy.rb",
    "app/policies/group_member_policy.rb",
    "app/policies/deploy_key_policy.rb",
    "app/policies/concerns/policy_actor.rb",
    "app/policies/concerns/member_policy_helpers.rb",
    "ee/app/policies/ee/project_policy.rb",
    "ee/app/policies/ee/group_policy.rb",
    "ee/app/policies/epic_policy.rb",
    "lib/gitlab/allowable.rb",

    # =================================================================================
    # Membership, roles and privilege assignment reachable by any signed-in user
    # =================================================================================
    "app/models/member.rb",
    "app/models/members/project_member.rb",
    "app/models/members/group_member.rb",
    "app/models/project_team.rb",
    "app/services/members/create_service.rb",
    "app/services/members/update_service.rb",
    "app/services/members/invite_service.rb",
    "app/services/users/build_service.rb",
    "app/services/users/update_service.rb",
    "ee/app/models/members/member_role.rb",
    "ee/app/services/ee/members/create_service.rb",
    "app/controllers/invites_controller.rb",
    "lib/api/members.rb",
    "lib/api/invitations.rb",
    "lib/api/access_requests.rb",
    "lib/api/helpers/members_helpers.rb",

    # =================================================================================
    # Authentication, sessions, 2FA, OAuth and SSO entrypoints
    # =================================================================================
    "lib/gitlab/auth.rb",
    "lib/gitlab/auth/auth_finders.rb",
    "lib/gitlab/auth/request_authenticator.rb",
    "lib/gitlab/auth/current_user_mode.rb",
    "lib/gitlab/auth/scope_validator.rb",
    "lib/gitlab/auth/two_factor_auth_verifier.rb",
    "lib/gitlab/auth/o_auth/user.rb",
    "lib/gitlab/auth/saml/user.rb",
    "lib/gitlab/auth/otp/strategies/devise.rb",
    "app/controllers/sessions_controller.rb",
    "app/controllers/passwords_controller.rb",
    "app/controllers/registrations_controller.rb",
    "app/controllers/omniauth_callbacks_controller.rb",
    "app/controllers/oauth/authorizations_controller.rb",
    "app/controllers/concerns/authenticates_with_two_factor.rb",
    "app/controllers/concerns/enforces_two_factor_authentication.rb",
    "app/controllers/concerns/verifies_with_email.rb",
    "app/controllers/application_controller.rb",
    "app/controllers/concerns/routable_actions.rb",
    "ee/lib/gitlab/auth/group_saml/sso_enforcer.rb",

    # =================================================================================
    # Tokens: PATs, deploy tokens, OAuth grants, LFS and registry tokens
    # =================================================================================
    "app/models/personal_access_token.rb",
    "app/models/deploy_token.rb",
    "app/models/deploy_key.rb",
    "app/models/oauth_access_token.rb",
    "app/models/doorkeeper/openid_connect/request.rb",
    "app/models/concerns/token_authenticatable.rb",
    "app/services/personal_access_tokens/create_service.rb",
    "app/services/auth/container_registry_authentication_service.rb",
    "lib/gitlab/lfs_token.rb",
    "lib/api/api_guard.rb",
    "lib/api/helpers/authentication.rb",

    # =================================================================================
    # REST API surface: authorization helpers and high-value endpoints
    # =================================================================================
    "lib/api/api.rb",
    "lib/api/base.rb",
    "lib/api/helpers.rb",
    "lib/api/projects.rb",
    "lib/api/groups.rb",
    "lib/api/users.rb",
    "lib/api/files.rb",
    "lib/api/repositories.rb",
    "lib/api/merge_requests.rb",
    "lib/api/issues.rb",
    "lib/api/notes.rb",
    "lib/api/discussions.rb",
    "lib/api/snippets.rb",
    "lib/api/project_snippets.rb",
    "lib/api/markdown.rb",
    "lib/api/markdown_uploads.rb",
    "lib/api/internal/base.rb",
    "lib/api/helpers/internal_helpers.rb",
    "lib/api/helpers/projects_helpers.rb",
    "lib/api/validations/validators/file_path.rb",
    "lib/api/entities/user.rb",

    # =================================================================================
    # GraphQL authorization layer and query entrypoint
    # =================================================================================
    "app/graphql/gitlab_schema.rb",
    "app/graphql/graphql_triggers.rb",
    "app/graphql/types/base_field.rb",
    "app/graphql/types/base_object.rb",
    "lib/gitlab/graphql/authorize/authorize_resource.rb",
    "lib/gitlab/graphql/authorize/field_extension.rb",
    "lib/gitlab/graphql/authorize/object_authorization.rb",
    "lib/api/glql.rb",

    # =================================================================================
    # Finders and search: where cross-tenant data leaks are decided
    # =================================================================================
    "app/finders/projects_finder.rb",
    "app/finders/groups_finder.rb",
    "app/finders/issues_finder.rb",
    "app/finders/merge_requests_finder.rb",
    "app/finders/notes_finder.rb",
    "app/finders/snippets_finder.rb",
    "app/finders/users_finder.rb",
    "app/finders/todos_finder.rb",
    "app/finders/members_finder.rb",
    "app/finders/group_members_finder.rb",
    "app/finders/ci/jobs_finder.rb",
    "app/finders/ci/pipelines_finder.rb",
    "app/finders/concerns/finder_with_cross_project_access.rb",
    "app/services/search_service.rb",
    "lib/gitlab/search_results.rb",
    "app/models/project_feature.rb",
    "app/models/concerns/featurable.rb",

    # =================================================================================
    # CI/CD: job tokens, variables, pipeline creation and included config
    # =================================================================================
    "app/models/ci/job_token/scope.rb",
    "app/models/ci/job_token/allowlist.rb",
    "app/models/ci/job_token/authorization.rb",
    "app/models/ci/job_token/project_scope_link.rb",
    "app/models/ci/job_token/group_scope_link.rb",
    "lib/ci/job_token/jwt.rb",
    "lib/ci/job_token/policies.rb",
    "lib/ci/job_token/middleware.rb",
    "lib/api/project_job_token_scope.rb",
    "app/models/ci/build.rb",
    "app/models/ci/variable.rb",
    "app/models/ci/group_variable.rb",
    "app/models/concerns/ci/has_variable.rb",
    "app/services/ci/create_pipeline_service.rb",
    "lib/gitlab/ci/build/policy/refs.rb",
    "lib/gitlab/ci/config.rb",
    "lib/gitlab/ci/config/external/mapper.rb",
    "lib/gitlab/ci/config/external/file/base.rb",
    "lib/api/ci/runner.rb",
    "lib/api/ci/jobs.rb",
    "lib/api/ci/job_artifacts.rb",
    "lib/api/ci/secure_files.rb",
    "app/services/ci/job_artifacts/create_service.rb",
    "app/controllers/projects/artifacts_controller.rb",
    "ee/app/models/protected_environment.rb",

    # =================================================================================
    # Git access control: repository read/write, protected refs, push checks
    # =================================================================================
    "lib/gitlab/git_access.rb",
    "lib/gitlab/git_access_project.rb",
    "lib/gitlab/git_access_snippet.rb",
    "lib/gitlab/git_access_wiki.rb",
    "lib/gitlab/user_access.rb",
    "lib/gitlab/checks/changes_access.rb",
    "lib/gitlab/checks/single_change_access.rb",
    "lib/gitlab/checks/branch_check.rb",
    "lib/gitlab/checks/tag_check.rb",
    "lib/gitlab/checks/push_check.rb",
    "lib/gitlab/checks/diff_check.rb",
    "lib/gitlab/checks/lfs_check.rb",
    "lib/gitlab/checks/snippet_check.rb",
    "app/models/protected_branch.rb",
    "app/models/concerns/protected_ref_access.rb",
    "ee/lib/ee/gitlab/git_access.rb",
    "app/controllers/repositories/git_http_controller.rb",
    "app/controllers/repositories/lfs_api_controller.rb",
    "app/controllers/projects/raw_controller.rb",
    "app/controllers/projects/blob_controller.rb",

    # =================================================================================
    # File handling: uploads, object storage, path traversal and Workhorse trust
    # =================================================================================
    "app/uploaders/gitlab_uploader.rb",
    "app/uploaders/file_uploader.rb",
    "app/uploaders/personal_file_uploader.rb",
    "app/uploaders/namespace_file_uploader.rb",
    "app/uploaders/job_artifact_uploader.rb",
    "app/uploaders/lfs_object_uploader.rb",
    "app/uploaders/object_storage.rb",
    "app/uploaders/workhorse.rb",
    "app/uploaders/content_type_whitelist.rb",
    "app/uploaders/file_mover.rb",
    "app/uploaders/records_uploads.rb",
    "app/models/upload.rb",
    "app/controllers/concerns/uploads_actions.rb",
    "app/controllers/concerns/send_file_upload.rb",
    "app/controllers/projects/uploads_controller.rb",
    "lib/gitlab/workhorse.rb",
    "lib/gitlab/middleware/multipart.rb",
    "lib/gitlab/path_traversal.rb",
    "lib/gitlab/file_finder.rb",

    # =================================================================================
    # Package and dependency registries reachable with a token
    # =================================================================================
    "lib/api/maven_packages.rb",
    "lib/api/npm_project_packages.rb",
    "lib/api/generic_packages.rb",
    "lib/api/nuget_project_packages.rb",
    "lib/api/pypi_packages.rb",
    "lib/api/helpers/packages_helpers.rb",
    "lib/api/dependency_proxy.rb",
    "app/models/packages/package.rb",

    # =================================================================================
    # Outbound requests: SSRF surface in webhooks, integrations and imports
    # =================================================================================
    "gems/gitlab-http/lib/gitlab/http_v2/url_blocker.rb",
    "gems/gitlab-http/lib/gitlab/http_v2/new_connection_adapter.rb",
    "gems/gitlab-http/lib/gitlab/http_v2/url_allowlist.rb",
    "gems/gitlab-http/lib/gitlab/http_v2/ip_allowlist_entry.rb",
    "gems/gitlab-http/lib/gitlab/http_v2/domain_allowlist_entry.rb",
    "gems/gitlab-http/lib/gitlab/http_v2/client.rb",
    "lib/gitlab/http.rb",
    "lib/gitlab/url_sanitizer.rb",
    "app/services/web_hook_service.rb",
    "app/models/hooks/web_hook.rb",
    "app/models/hooks/project_hook.rb",
    "lib/gitlab/middleware/go.rb",

    # =================================================================================
    # Import/export: attacker-supplied archives and remote import payloads
    # =================================================================================
    "lib/gitlab/import_export/project/tree_restorer.rb",
    "lib/gitlab/import_export/attribute_cleaner.rb",
    "lib/gitlab/import_export/file_importer.rb",
    "lib/gitlab/import_export/command_line_util.rb",
    "lib/gitlab/import_export/decompressed_archive_size_validator.rb",
    "app/services/import/github_service.rb",
    "lib/api/bulk_imports.rb",
    "lib/api/group_import.rb",

    # =================================================================================
    # Markdown rendering and sanitization: stored XSS surface
    # =================================================================================
    "lib/banzai/pipeline/base_pipeline.rb",
    "lib/banzai/pipeline/full_pipeline.rb",
    "lib/banzai/pipeline/gfm_pipeline.rb",
    "lib/banzai/filter/base_sanitization_filter.rb",
    "lib/banzai/filter/sanitization_filter.rb",
    "lib/banzai/filter/markdown_filter.rb",
    "lib/banzai/filter/autolink_filter.rb",
    "lib/banzai/filter/external_link_filter.rb",
    "lib/banzai/filter/image_link_filter.rb",
    "lib/banzai/filter/iframe_link_filter.rb",
    "lib/banzai/filter/math_filter.rb",
    "lib/banzai/filter/color_filter.rb",
    "lib/banzai/filter/asset_proxy_filter.rb",
    "lib/banzai/filter/base_relative_link_filter.rb",
    "lib/banzai/filter/reference_redactor_filter.rb",
    "lib/banzai/filter/references/reference_filter.rb",
    "lib/banzai/filter/references/abstract_reference_filter.rb",
    "lib/banzai/filter/references/user_reference_filter.rb",
    "lib/banzai/filter/ascii_doc_sanitization_filter.rb",

    # =================================================================================
    # Issuables, notes and quick actions: cross-object authorization at write time
    # =================================================================================
    "app/services/notes/create_service.rb",
    "app/services/issues/create_service.rb",
    "app/services/merge_requests/create_service.rb",
    "app/services/merge_requests/update_service.rb",
    "app/services/merge_requests/merge_service.rb",
    "app/services/merge_requests/refresh_service.rb",
    "app/services/quick_actions/interpret_service.rb",
    "lib/gitlab/quick_actions/issue_actions.rb",
    "app/models/concerns/issuable.rb",
    "app/models/concerns/mentionable.rb",
    "app/models/concerns/participable.rb",
    "app/models/todo.rb",
    "app/models/event.rb",
    "app/models/wiki_page.rb",
    "ee/app/models/approval_merge_request_rule.rb",

    # =================================================================================
    # Namespace lifecycle: transfer, fork and visibility changes that re-scope access
    # =================================================================================
    "app/models/project.rb",
    "app/models/group.rb",
    "app/models/namespace.rb",
    "app/models/user.rb",
    "app/services/projects/transfer_service.rb",
    "app/services/projects/fork_service.rb",
    "app/services/projects/update_service.rb",
    "app/services/projects/create_service.rb",
    "app/services/groups/transfer_service.rb",
    "app/services/groups/update_service.rb",
]


target_scopes = [
    "Critical. An unauthenticated visitor or a signed-in user with no membership reads private repository content, issues, merge requests, snippets, or CI data of a project or group they do not belong to, because a REST endpoint, GraphQL field, finder scope, or policy rule in projects_finder.rb, issues_finder.rb, gitlab_schema.rb, object_authorization.rb, or project_policy.rb resolves the object before or without the permission check that was supposed to gate it.",
    "Critical. A Guest, Reporter, or Developer gains Maintainer, Owner, or instance-admin capability, because role comparison, invite acceptance, access-request approval, custom member-role ability mapping, or the highest-role resolution in member.rb, project_team.rb, members/create_service.rb, members/update_service.rb, invitations, or member_role.rb lets a user grant themselves or accept an access level above the one the inviter actually held.",
    "Critical. An attacker authenticates as another user or hijacks their session, because session fixation and rotation in sessions_controller.rb, the 2FA gate in authenticates_with_two_factor.rb and enforces_two_factor_authentication.rb, email verification in verifies_with_email.rb, password reset in passwords_controller.rb, the OAuth authorize and redirect flow, or identity linking in o_auth/user.rb and saml/user.rb binds a credential, OTP, reset token, or external identity to the wrong account or accepts it at the wrong step.",
    "Critical. A low-scope credential performs actions it was never granted, because scope enforcement in scope_validator.rb and api_guard.rb, token resolution in auth_finders.rb and request_authenticator.rb, deploy-token and deploy-key authorization, registry JWT issuance in container_registry_authentication_service.rb, or LFS token handling lets a read-only, expired, revoked, or project-bound token write data, reach another project, or escalate into a full session.",
    "Critical. A pipeline job in an attacker-controlled project reads or writes another project's data, because CI_JOB_TOKEN scope resolution in ci/job_token/scope.rb, allowlist.rb, policies.rb, and jwt.rb, or the job-token middleware, honors an inbound allowlist entry, group scope link, or policy that the target project never granted, turning a public-project fork pipeline into cross-project repository, package, or artifact access.",
    "Critical. Protected CI variables, masked secrets, or secure files reach a job an attacker controls, because protected-ref matching in ci/build/policy/refs.rb and protected_branch.rb, variable exposure in ci/variable.rb and has_variable.rb, environment scoping in protected_environment.rb, or include resolution in ci/config/external/mapper.rb lets a merge request, tag, or crafted ref name from an unprivileged contributor run with credentials reserved for protected refs.",
    "Critical. The GitLab server reads, writes, or executes a file outside the intended directory, because path construction and traversal checks in path_traversal.rb, file_uploader.rb, personal_file_uploader.rb, job_artifact_uploader.rb, file_mover.rb, uploads_actions.rb, send_file_upload.rb, package-registry filename validation, or import extraction in file_importer.rb and command_line_util.rb accepts an attacker-supplied name, version, path, or archive entry that escapes its namespace.",
    "Critical. An attacker makes the GitLab backend issue requests to internal services or cloud metadata endpoints, because address validation in http_v2/url_blocker.rb, new_connection_adapter.rb, url_allowlist.rb, or url_sanitizer.rb can be defeated by DNS rebinding, redirect following, IPv6 or octal encoding, userinfo, or a URL shape accepted by a webhook, integration, repository import, dependency proxy, or go-import request that any user can configure.",
    "High. Markup a user controls executes JavaScript in another user's session, because the sanitization allowlist in base_sanitization_filter.rb and sanitization_filter.rb, or attribute and URL handling in autolink_filter.rb, image_link_filter.rb, iframe_link_filter.rb, math_filter.rb, color_filter.rb, external_link_filter.rb, or the AsciiDoc path, lets an issue, note, wiki page, snippet, or file rendered through the GFM pipeline survive with a scriptable attribute or scheme, leading to token or session theft.",
    "High. A user reads or writes repository refs they are not authorized for over Git, because access resolution in git_access.rb, git_access_project.rb, git_access_snippet.rb, git_access_wiki.rb, user_access.rb, or the push checks in changes_access.rb, branch_check.rb, tag_check.rb, and diff_check.rb resolves the project, ref, or actor differently than the policy layer, allowing a push to a protected branch, a fork-to-upstream write, or a clone of a private repository.",
    "High. Access survives the change that was supposed to revoke it, because project transfer, group transfer, fork, visibility downgrade, membership removal, or feature-access changes in projects/transfer_service.rb, groups/transfer_service.rb, fork_service.rb, projects/update_service.rb, project_feature.rb, and featurable.rb leave stale membership, cached authorizations, todos, events, or fork-network links that let a removed or now-outside user keep reading or writing the object.",
    "Critical/High blind spot. An unauthenticated visitor, a free signed-in user, or a Guest abuses an assumption GitLab never wrote down: an object authorized as one type and then acted on as another, a permission checked on the parent but enforced against a re-fetched child, a rule enforced in the web path but absent from its REST, GraphQL, Git, webhook, or background-job twin, an identifier resolved by path or ID after the check that approved it, or a partially committed write on an error path - yielding private-data disclosure across tenants, a role or token scope the attacker was never granted, or code execution on the GitLab server.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit questions for one GitLab target.

    ```
    target_file format:
    "'File Name: app/policies/project_policy.rb -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact GitLab target:

    {target_file}

    Project focus:
    gitlabhq is the GitLab Rails application (CE and EE). Focus only on what an unauthenticated visitor or an ordinary signed-in user reaches over HTTP or Git: public pages, the REST API, GraphQL, Git HTTP/SSH, webhooks and integrations they configure in their own namespace, repository imports, uploads and package registries, CI pipelines in their own or a forked project, and markup they can render into another user's browser.

    Rules:
    * Treat `File Name:` as the exact file/class.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Ruby symbols (class, module, method, policy rule/condition, ability name, Grape endpoint, GraphQL field, scope, concern) when possible.
    * Attacker is unprivileged only: an unauthenticated visitor, a free signed-in user, a Guest or Reporter on a public project, an outside contributor pushing to their own fork, or the Owner of their own personal namespace. They hold only their own credentials.
    * Attacker is NOT an instance admin, auditor, Owner or Maintainer of the victim namespace, GitLab operator, runner owner, database or object-storage holder, or holder of another user's token, session, or SSH key. Never assume a leaked credential, compromised host, malicious runner or Gitaly node, non-default instance configuration, or social engineering.
    * Out of scope, never ask about: anything needing admin or victim-Maintainer rights, rate limiting, denial of service, resource exhaustion, missing security headers, self-XSS, clickjacking, user or content enumeration, email spoofing, CSRF with no state change, verbose errors, dependency CVEs without a reachable GitLab path, Geo and Sidekiq-operator paths, or instance-setting misconfiguration.
    * Ignore spec/, test/, qa/, fixtures, factories, mocks, benchmarks, docs, generated files, migrations, and config-only findings.
    * Every question must describe a real HTTP request, GraphQL document, Git operation, uploaded file, pipeline, or rendered markup the attacker actually sends. No generic unbounded-allocation, memory-growth, cache-size, N+1, or resource-exhaustion speculation; no "what if the input is huge" questions without a concrete submitted payload and a concrete broken invariant.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target cross-tenant disclosure of private data, privilege escalation to a higher role or admin, authentication bypass or account takeover, token-scope bypass, CI job-token or protected-variable compromise, remote code execution, arbitrary file read/write, SSRF into internal services, or stored XSS leading to session or token theft.
    * Every question must be testable by an RSpec request spec, policy spec, GraphQL spec, service or lib spec, or an exact sequence against a local GDK instance.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization is exact: every read and write is allowed by a policy ability evaluated against the acting user and the exact object being touched, at the moment it is touched.
    * Privilege is monotonic: no user can obtain a role, member role ability, or token scope broader than one already granted to them by someone who held it.
    * Identity is bound: a session, OTP, reset token, invite, or external identity authenticates exactly the account it was issued for.
    * Tenancy holds: data of one project, group, or user is never returned, written, or joined into the response for an actor outside its visibility and membership.
    * Secrets stay in scope: CI variables, job tokens, secure files, and registry credentials reach only jobs on refs and projects authorized to hold them.
    * Input stays data: user-supplied paths, URLs, archives, and markup never become filesystem locations, internal requests, commands, or executable script in another user's browser.
    * Enforcement is uniform: a rule enforced on one entrypoint is enforced identically on its REST, GraphQL, Git, webhook, and background-job twins.

    Each question must include:
    1. target class/method (or policy rule, endpoint, or GraphQL field);
    2. attacker action (a concrete HTTP request, GraphQL document, Git operation, upload, pipeline, or rendered markup);
    3. preconditions (accounts, roles, tokens, projects, and forks the attacker controls);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: RSpec request/policy/GraphQL/service spec or GDK steps PARAMETERS and assert AUTHORIZATION_EXACTNESS, PRIVILEGE_MONOTONICITY, IDENTITY_BINDING, TENANCY, SECRET_SCOPING, INPUT_STAYS_DATA, or UNIFORM_ENFORCEMENT.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused GitLab exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: an unauthenticated visitor, a free signed-in user, a Guest or Reporter on a public project, an outside contributor pushing to their own fork, or the Owner of their own personal namespace, holding only their own credentials.
- Reject anything needing instance-admin, auditor, victim-Maintainer or victim-Owner rights, operator or runner access, database or object-storage access, another user's token, session or SSH key, a non-default instance setting, or social engineering.
- Reject DoS, rate-limit, resource-exhaustion, missing-header, self-XSS, clickjacking, enumeration, email-spoofing, no-impact CSRF, verbose-error, dependency-only, Geo/Sidekiq-operator, and spec/qa/fixture/factory/docs/generated/migration/config-only findings.
- Reject generic unbounded-allocation or performance claims with no concrete submitted request and no broken invariant.
- This program pays High and Critical only. Focus on real impact: cross-tenant disclosure of private repository, issue, MR, snippet or CI data, privilege escalation to a higher role or admin, authentication bypass or account takeover, token-scope bypass, CI job-token or protected-variable compromise, remote code execution, arbitrary file read/write, SSRF into internal services or cloud metadata, or stored XSS leading to session or token theft.

## Validate
- Trace the exact reachable path from the attacker's HTTP request, GraphQL document, Git operation, upload, pipeline, or rendered markup into the affected method.
- Check whether the policy layer, `authorize!`/`can?` calls, Grape or GraphQL authorization, finder scoping, strong parameters, sanitization filters, path-traversal checks, or URL blocking already stop it.
- Confirm the path is reachable on the current default configuration of the tier the file belongs to (CE for `app/`, `lib/`; EE for `ee/`), with default feature flag state.
- Accept only concrete unauthorized data access, role or scope escalation, account takeover, secret compromise, code execution, arbitrary file access, internal SSRF, or script execution in another user's session.
- Require exact file/method support and a reproducible RSpec request, policy, GraphQL, service, or lib spec, or exact GDK steps.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker request inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (remote code execution, account takeover, authentication bypass, admin escalation, mass cross-tenant private data disclosure, CI secret or job-token compromise) or High (authorization bypass, role escalation, targeted private data disclosure, arbitrary file read/write, internal SSRF, stored XSS leading to session or token theft)]

### Likelihood Explanation
[Preconditions, accounts, roles and tokens needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[RSpec spec or GDK request sequence with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for GitLab.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs an unauthenticated visitor, a free signed-in user, a Guest or Reporter on a public project, or an outside fork contributor can reach: policies and abilities, membership and roles, authentication and sessions, token scopes, the REST API, GraphQL, finders and search, CI job tokens and variables, Git access and push checks, uploads and package registries, outbound request validation, import/export, or markdown sanitization.
- Reject admin-only, operator-only, runner-owner, Geo, database, leaked-credential, misconfiguration-only, DoS, rate-limit, enumeration, self-XSS, header-only, dependency-only, and spec/qa/fixture/docs/generated/config-only paths, and no-impact analogs.
- Medium , High and Critical only; no low, or resource-only analogs.

## Validate
- Map the bug class to the strongest reachable GitLab path from a single HTTP request, GraphQL document, Git operation, upload, or pipeline.
- Prove root cause with exact file/method support.
- Accept only concrete cross-tenant data disclosure, role or token-scope escalation, authentication bypass or account takeover, CI secret or job-token compromise, code execution, arbitrary file read/write, internal SSRF, or stored XSS leading to session or token theft.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for GitLab security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- This program pays High and Critical only; reject low, medium, informational, best-practice, and resource-only reports.
- Reject DoS, rate-limit, resource-exhaustion, missing-header, cookie-flag, self-XSS, clickjacking, user or content enumeration, email/SPF/DMARC spoofing, no-impact CSRF, verbose-error, TLS-config, automated-scanner, dependency-only, Geo/Sidekiq-operator, docs/style, generated-file, and spec/qa/fixture/factory/migration/config-only issues.
- Reject if the exploit needs instance-admin, auditor, victim-Maintainer or victim-Owner rights, GitLab operator, runner, Gitaly, database or object-storage access, another user's token, session or SSH key, victim social engineering, a non-default instance configuration or feature flag, or anything outside what an unauthenticated visitor or ordinary signed-in user can put in an HTTP request, GraphQL document, Git operation, upload, pipeline, or rendered markup.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unauthenticated visitor, a free signed-in user, a Guest or Reporter on a public project, or an outside fork contributor, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope category: Critical - remote code execution on the GitLab server, authentication bypass or account takeover, escalation to instance admin, mass cross-tenant disclosure of private data, or CI job-token/protected-variable compromise across projects; High - authorization or policy bypass, role escalation within a namespace, targeted private repository/issue/MR/snippet/CI data disclosure, arbitrary file read or write, SSRF into internal services or cloud metadata, or stored XSS leading to session or token theft.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, class/method, and line/code references.
2. Clear root cause and broken authorization-exactness, privilege-monotonicity, identity-binding, tenancy, secret-scoping, input-stays-data, or uniform-enforcement invariant.
3. Reachable exploit path: preconditions (attacker-controlled accounts, roles, tokens, projects, forks) -> submitted HTTP request, GraphQL document, Git operation, upload, pipeline, or rendered markup -> trigger -> bad result.
4. Existing policy abilities, `authorize!`/`can?` calls, Grape and GraphQL authorization, finder scoping, strong parameters, sanitization filters, path-traversal checks, and URL blocking reviewed and shown insufficient.
5. Concrete in-scope High/Critical impact with realistic likelihood.
6. Reproducible proof path: RSpec request, policy, GraphQL, service or lib spec, or exact steps against a local GDK instance.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an unauthenticated visitor or ordinary signed-in user trigger this, without admin, operator, victim-Maintainer, host, or foreign-credential access?
- Does the code actually behave as claimed on the current default configuration and default feature flag state?
- Is the impact caused by this code, not by a misconfiguration, a dependency, or an operator action?
- Is the disclosure, escalation, takeover, or code execution concrete rather than hypothetical?
- Would a GitLab triager accept the proof-of-concept?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and GitLab bounty category]

## Likelihood Explanation
[Attacker capability, accounts, roles and tokens required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or RSpec request/policy/GraphQL/service spec plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
