"""Demo identity provider and ISTR roles (placeholder for FAB SSO / Entra ID).

RBAC is enforced in the workflow's ``rbac_validation`` gate. Only roles that
investigate or review suspicious activity may run an investigation. Usernames
below are synthetic test identities.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ISTRRole(str, Enum):
    AML_ANALYST = "aml_analyst"
    AML_INVESTIGATOR = "aml_investigator"
    MLRO = "mlro"
    COMPLIANCE_OFFICER = "compliance_officer"
    PLATFORM_ADMINISTRATOR = "platform_administrator"
    RELATIONSHIP_MANAGER = "relationship_manager"  # front office: must NOT see ISTR work (tipping-off risk)


# Roles allowed to run investigations. Relationship managers are excluded on purpose.
INVESTIGATION_ROLES: frozenset[str] = frozenset({
    ISTRRole.AML_ANALYST.value,
    ISTRRole.AML_INVESTIGATOR.value,
    ISTRRole.MLRO.value,
    ISTRRole.COMPLIANCE_OFFICER.value,
    ISTRRole.PLATFORM_ADMINISTRATOR.value,
})


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: str


_DEMO_USERS: dict[str, User] = {
    "analyst1": User("analyst1", "Synthetic AML Analyst", ISTRRole.AML_ANALYST.value),
    "investigator1": User("investigator1", "Synthetic AML Investigator", ISTRRole.AML_INVESTIGATOR.value),
    "mlro1": User("mlro1", "Synthetic MLRO", ISTRRole.MLRO.value),
    "compliance1": User("compliance1", "Synthetic Compliance Officer", ISTRRole.COMPLIANCE_OFFICER.value),
    "admin1": User("admin1", "Synthetic Platform Admin", ISTRRole.PLATFORM_ADMINISTRATOR.value),
    "rm1": User("rm1", "Synthetic Relationship Manager", ISTRRole.RELATIONSHIP_MANAGER.value),
}


def login(username: str) -> User:
    """Resolves a demo user. Unknown users get an unprivileged role and are blocked by RBAC."""
    return _DEMO_USERS.get(username, User(username, username, "unknown"))


def demo_users() -> list[User]:
    return list(_DEMO_USERS.values())
