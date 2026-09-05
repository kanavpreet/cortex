# Matik - Language Decision: Python

Date: 2025-12-17

Status: `proposed`

Collaborators: @alfredo-moreira

Supersedes: [002-golang-vs-python.md](002-golang-vs-python.md)

## Context

The original decision (002) chose Go for Matik's backend with a note that Python would be used for ML algorithms. After significant development (174 Go files) and evolving project requirements, a comprehensive re-evaluation was conducted to determine if the original decision still holds.

For detailed analysis, see: [Language Analysis Research](../research/language-analysis.md)

## Summary of Findings

A thorough analysis across 16 criteria resulted in:

| Category | Go | Python | Tie |
|----------|:--:|:------:|:---:|
| **Count** | 6 | 6 | 4 |

**Go advantages:**
- Performance & resource efficiency (marginal for I/O-bound workloads)
- Lambda/serverless deployment (if Historian decoupled)
- Dependency management simplicity
- Existing codebase (174 files)

**Python advantages:**
- Team proficiency (100% vs 10-20% Go)
- ML/AI ecosystem and future optionality
- MCP official SDK (planned feature)
- Industry alignment for AIOps platforms
- External contributor availability (3-4x larger talent pool)
- Development velocity for 40+ integrations

**Key insight:** While the technical tally is tied, the **human factors strongly favor Python**:
- 80-90% of the team cannot effectively contribute to Go code
- Post-MVP aggressive expansion requires velocity the team can only achieve in Python
- External gig contributors for 40+ integrations are 3-4x more likely to know Python

## Decision

**Matik can migrate to Python.**

Given the tied technical evaluation (6-6 with 4 ties), there is no clear winner from a purely technical standpoint. The decision ultimately comes down to two key factors:

1. **Core team contribution velocity:** How quickly do we need the full team contributing to the codebase? With 80-90% of the team proficient in Python vs 10-20% in Go, remaining with Go means a prolonged ramp-up period before the team can effectively contribute. If aggressive feature delivery is required post-MVP, Python enables immediate full-team participation.

2. **Migration cost vs. long-term velocity:** The migration from Go to Python (174 files across 4 services + shared common/ packages) represents a significant investment. However, Airchat will help accelerate this migration process by assisting with code translation, test generation, and maintaining consistency across the rewrite. This reduces the migration timeline and risk substantially.

The technical merits are balanced, but team proficiency and development velocity for the planned 40+ integrations make Python the pragmatic choice. Go's advantages (performance, dependency management) are real but marginal for Matik's I/O-bound workloads where LLM latency dominates.

## Migration Considerations

| Aspect | Estimate |
|--------|----------|
| Current Go codebase | 174 files |
| Migration approach | Incremental, service-by-service |
| Priority order | Catalog (user-facing) → Historian → Chronicler → Correlator |
| Shared code | common/ models, DAOs, clients must be migrated together |
| Risk mitigation | Parallel operation during transition; feature parity validation |

**Migration effort is significant** but justified by:
1. Long-term velocity gains (100% team contribution vs 10-20%)
2. MCP integration with official SDK
3. Future ML optionality preserved
4. Alignment with AIOps industry trends

## Consequences

1. **Short-term:** Migration effort and temporary velocity reduction
2. **Long-term:** Full team contribution, faster feature delivery, ML optionality
3. **Infrastructure:** Slightly higher memory footprint (mitigated by K8s scaling)
4. **Standards:** Adopt strict typing (mypy), poetry/uv for dependencies, comprehensive testing

## Python Standards (Required)

To mitigate Python's weaknesses identified in the analysis:

- **Type hints + mypy:** Enforce strict typing in CI
- **Poetry or uv:** Deterministic dependency management
- **Pydantic:** Data validation and serialization
- **pytest:** Comprehensive test coverage
- **ruff/black:** Code formatting and linting
