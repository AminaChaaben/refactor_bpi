from pydantic import BaseModel


class Vulnerability(BaseModel):
    cve_id: str
    severity: str


class Project(BaseModel):
    domain: str
    project_id: int
    mr_iid: int


class Policy(BaseModel):
    max_retries: int
    max_budget: float


class PolicyPayload(BaseModel):
    project: Project
    vulnerability: Vulnerability
    policy: Policy
    consumed_budget: float
