"""Ground-truth extraction and verification data shapes for the
root-cause-coverage audit.

These are intermediate values passed between the audit's own functions and
flattened into sheet rows at the edge; they never cross a network boundary or
get persisted to the database on their own.
"""

from sqlmodel import Field, SQLModel


class GroundTruth(SQLModel):
    """The LLM's answer to the three ground-truth extraction questions.

    Note: No table=True — this is an intermediate value, not persisted on
    its own (see the module docstring).
    """

    change_related: bool = Field(
        ...,
        description=(
            "Whether the incident was judged to be caused by a change (a "
            "deploy, config change, or infra change), vs. a vendor outage, "
            "hardware failure, or other non-change cause"
        ),
    )
    ownership: str = Field(
        ...,
        description="Whose change caused it: 'airbnb', 'vendor', or 'unknown'",
    )
    cited_identifier: str | None = Field(
        default=None,
        description=(
            "Verbatim text quoted from the source, or None if nothing qualifies"
        ),
    )
    quote_span: str | None = Field(
        default=None,
        description=(
            "The surrounding sentence cited_identifier was pulled from — "
            "persisted as audit evidence so a human reviewer can see exactly "
            "what the LLM saw"
        ),
    )
    reasoning: str = Field(..., description="One-sentence rationale for the answer")
    source_traceable: bool | None = Field(
        default=None,
        description=(
            "Whether the root cause has some traceable external source even "
            "without a verified PR/TCMR -- e.g. a vendor outage reflected on "
            "a public status page (True), vs. an untracked internal change, "
            "hardware failure, or other cause with no fetchable record "
            "(False). None means genuinely ambiguous -- a human decides. "
            "Ignored (always treated as True) once a root cause is actually "
            "verified, regardless of this field."
        ),
    )


class VerifiedEntity(SQLModel):
    """A ground-truth citation confirmed to exist against its real source.

    ``entity_id`` matches the shape ``ReliabilityCorrelation.entity_id`` uses
    for the same entity type, so classification can compare them directly.

    Note: No table=True — this is an intermediate value, not persisted on
    its own (see the module docstring).
    """

    entity_type: str = Field(..., description="'github_pr' or 'jira_tcmr'")
    entity_id: str = Field(
        ...,
        description="PR id (as str) for github_pr, issue key for jira_tcmr",
    )
    org: str | None = Field(default=None, description="GitHub org, for github_pr")
    repo: str | None = Field(default=None, description="GitHub repo, for github_pr")
