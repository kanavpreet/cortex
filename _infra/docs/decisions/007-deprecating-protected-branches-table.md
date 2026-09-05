# ADR 005: Deprecating GHE Protected Branches Table

## Status

Date: 2025-11-13

Status: `Accepted`

Collaborators: @sumit_chachadi

## Context

The Matik platform currently maintains a dedicated `ghe_protected_branches` table to track protected branches for GitHub Enterprise repositories. This table was originally designed to:

1. Store metadata about protected branches in repositories
2. Serve as a foreign key reference for pull requests targeting protected branches
3. Enable filtering and analysis of PRs based on branch protection status
4. Determine environments (e.g., production, staging) based on branch protection rules

However, the implementation has revealed significant operational challenges:

### Current Implementation Issues

1. **High API Call Volume**: The current approach requires:
   - Fetching all branches in a repository
   - Making individual API calls to check protection status for each branch
   - This results in `O(n)` API calls where `n` = number of branches per repository

2. **Time-Consuming Operations**:
   - For repositories with many branches, the protection check process becomes a bottleneck
   - Slows down the overall PR crawling process
   - Increases risk of hitting GitHub API rate limits

3. **Redundant Data Storage**:
   - Branch information (including target branch) is already available in the PR API response
   - Environment determination can be derived from branch naming conventions
   - The protected branches table adds unnecessary complexity to the data model

### Current Data Flow

```
Historian Crawler -> Fetch Repos -> Fetch All Branches -> Check Protection (N API calls) -> Store Protected Branches

Fetch PRs ------------------------------------------------> Link PR to Protected Branch (FK)
```

## Decision

We will **deprecate and remove** the `ghe_protected_branches` table and simplify the data model by:

1. **Removing the Protected Branches Table**
   - Drop the `ghe_protected_branches` table from the database schema
   - Remove related DAO operations and models

2. **Restructuring Pull Requests Table**
   - Add `repository_id` as a direct foreign key to `ghe_repositories` table
   - Store target branch name directly in the `ghe_pull_requests` table (already exists as `target_branch_name`)
   - Derive environment information from branch naming conventions at query time

3. **Simplified Data Flow**

```
Historian Crawler -> Fetch Repos -> Fetch PRs (with branch info) -> Store PRs with Repository ID
```

## Consequences

### Positive

1. **Reduced API Calls**
   - Eliminates the need to fetch and check all branches
   - Reduces API call volume by ~50-80% depending on repository branch count
   - Lower risk of hitting GitHub API rate limits

2. **Improved Performance**
   - Faster crawler execution time
   - Reduced database complexity (fewer tables and foreign key constraints)
   - Simpler query patterns

3. **Simplified Architecture**
   - Cleaner data model with fewer dependencies
   - Easier to maintain and understand
   - Reduced code complexity in crawler logic

4. **Cost Reduction**
   - Less API usage
   - Reduced database storage
   - Lower compute time for historian service

### Negative

1. **Loss of Branch Protection Metadata**
   - No longer storing explicit protection rules (required status checks, enforce admins, etc.)
   - **Mitigation**: This metadata was not being actively used in current features

2. **Environment Determination**
   - Environment must be inferred from branch naming conventions rather than explicit data
   - **Mitigation**: This approach is already being used and works reliably with standard naming patterns

3. **Historical Data**
   - Existing data in `ghe_protected_branches` table will be lost
   - **Mitigation**: No active features depend on this historical data

## Metrics for Success

1. **API Call Reduction**: 50-80% reduction in GitHub API calls per crawler run
2. **Crawler Performance**: 30-50% reduction in total execution time
3. **Code Simplicity**: 200-300 lines of code removed
4. **Database Performance**: Simpler query patterns, faster PR lookups

## References

- [GitHub REST API - Branches](https://docs.github.com/en/rest/branches/branches)
- [GitHub REST API - Pull Requests](https://docs.github.com/en/rest/pulls/pulls)
- [Matik C4 Diagram](https://lucid.app/lucidchart/6c6d807b-ca61-4be7-b571-897e35bd9712)
