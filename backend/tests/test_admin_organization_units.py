import pytest
from fastapi import HTTPException

from app.api.admin import _unit_relation_payload, _validate_unit_parent


@pytest.mark.parametrize(
    ("unit_type", "parent_type"),
    [
        ("company", "group"),
        ("division", "company"),
        ("department", "company"),
        ("department", "division"),
        ("team", "department"),
        ("project", "team"),
    ],
)
def test_validate_allowed_organization_unit_parent(unit_type, parent_type):
    _validate_unit_parent(unit_type, parent_type)


@pytest.mark.parametrize(
    ("unit_type", "parent_type"),
    [
        ("company", "division"),
        ("division", "department"),
        ("department", "team"),
        ("team", "company"),
        ("group", "company"),
    ],
)
def test_validate_rejects_invalid_organization_unit_parent(unit_type, parent_type):
    with pytest.raises(HTTPException) as exc_info:
        _validate_unit_parent(unit_type, parent_type)
    assert exc_info.value.status_code == 422


def test_only_group_can_have_no_parent():
    _validate_unit_parent("group", None)
    with pytest.raises(HTTPException) as exc_info:
        _validate_unit_parent("company", None)
    assert exc_info.value.status_code == 422


def test_parent_relation_payload_reparents_active_unit():
    relations = _unit_relation_payload(
        unit_code="dept_a",
        old_parent_code="company_a",
        new_parent_code="company_b",
        old_status="active",
        new_status="active",
    )
    assert relations == {
        "writes": [
            {"user": "unit:dept_a", "relation": "child", "object": "unit:company_b"}
        ],
        "deletes": [
            {"user": "unit:dept_a", "relation": "child", "object": "unit:company_a"}
        ],
    }


def test_parent_relation_payload_avoids_noop_write():
    relations = _unit_relation_payload(
        unit_code="dept_a",
        old_parent_code="company_a",
        new_parent_code="company_a",
        old_status="active",
        new_status="active",
    )
    assert relations == {"writes": [], "deletes": []}


def test_parent_relation_payload_deactivates_and_reactivates_unit():
    deactivate = _unit_relation_payload(
        unit_code="dept_a",
        old_parent_code="company_a",
        new_parent_code="company_a",
        old_status="active",
        new_status="inactive",
    )
    reactivate = _unit_relation_payload(
        unit_code="dept_a",
        old_parent_code="company_a",
        new_parent_code="company_a",
        old_status="inactive",
        new_status="active",
    )
    assert deactivate == {
        "writes": [],
        "deletes": [
            {"user": "unit:dept_a", "relation": "child", "object": "unit:company_a"}
        ],
    }
    assert reactivate == {
        "writes": [
            {"user": "unit:dept_a", "relation": "child", "object": "unit:company_a"}
        ],
        "deletes": [],
    }
