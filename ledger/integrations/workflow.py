"""Durable Accounts tasks/notifications under recorded manager authorization."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from ledger import models as m
from ledger.services.common import full_scope, check, make, event, accounts_department


@transaction.atomic
def authorize_routing(user, enabled, channels, reason):
    full_scope(user, "add_approvalroutingpolicy")
    from tasks.services.access import can_assign_to_department

    department = accounts_department()
    check(
        can_assign_to_department(user, department),
        "Only staff authorized to assign to Accounts may authorize automatic task routing.",
    )
    check(reason.strip(), "Record the routing authorization reason.")
    check(
        set(channels) <= {"DASHBOARD", "EMAIL"},
        "Select dashboard/email; WhatsApp delivery is not configured.",
    )
    policy = (
        m.ApprovalRoutingPolicy.objects.select_for_update()
        .filter(department=department)
        .first()
    )
    if policy:
        full_scope(user, "change_approvalroutingpolicy")
        policy.enabled = enabled
        policy.channels = channels
        policy.reason = reason
        policy.created_by = user
        policy.created_by_name = user.get_full_name() or user.username
        policy.save(
            update_fields=[
                "enabled",
                "channels",
                "reason",
                "created_by",
                "created_by_name",
            ]
        )
    else:
        policy = make(
            m.ApprovalRoutingPolicy,
            user,
            department=department,
            enabled=enabled,
            channels=channels,
            reason=reason,
        )
    event(
        user,
        "routing_authorized",
        reason=reason,
        policy=policy.pk,
        enabled=enabled,
        channels=channels,
    )
    return policy


def schedule(intent):
    transaction.on_commit(lambda: process_one(intent.pk))


def process_one(pk):
    try:
        with transaction.atomic():
            intent = m.IntegrationIntent.objects.select_for_update().get(pk=pk)
            if intent.processed_at:
                return
            approval = m.ApprovalRequest.objects.select_for_update().get(
                pk=intent.approval_id
            )
            kind = (
                "quotation"
                if approval.quotation_revision_id
                else m.RESOURCE[approval.revision.record.kind]
            )
            users = [
                u
                for u in get_user_model().objects.filter(is_active=True, is_staff=True)
                if u.has_perm("ledger.approve_" + kind)
                and u.has_perm("ledger.viewall_financialrecord")
                and u.has_perm("ledger.view_approvalrequest")
            ]
            policy = (
                m.ApprovalRoutingPolicy.objects.filter(
                    department=accounts_department(), enabled=True
                )
                .select_related("created_by", "department")
                .first()
            )
            from notifications.services import notify

            if users:
                notify(
                    recipients=users,
                    notification_type="ledger.approval",
                    category="ledger",
                    title="Accounts approval " + intent.event.lower(),
                    message="Review the financial approval queue.",
                    channels=policy.channels
                    if policy and policy.channels
                    else ["DASHBOARD"],
                    actor=intent.created_by,
                    source=approval,
                    metadata={
                        "approval": str(approval.pk),
                        "path": "/ledger/approval-requests/",
                    },
                    deduplication_key=f"ledger:{approval.pk}:{intent.event}",
                )
            if approval.status == "PENDING":
                if not policy:
                    intent.failure = "task_routing_unconfigured"
                    intent.attempts += 1
                    intent.save(update_fields=["failure", "attempts"])
                    return
                from tasks.services.access import can_assign_to_department

                check(
                    policy.created_by
                    and policy.created_by.is_active
                    and can_assign_to_department(policy.created_by, policy.department),
                    "Manager routing authorization is no longer valid.",
                )
                profiles = [
                    u.profile
                    for u in users
                    if getattr(u, "profile", None)
                    and u.profile.department_id == policy.department_id
                ]
                check(
                    profiles,
                    "No currently authorized Accounts approvers can receive the task.",
                )
                if not approval.task_batch_id:
                    from tasks.models import Task, TaskBatch
                    from tasks.services.assignments import _build_task
                    from tasks.services.activities import create_batch_activity

                    batch = TaskBatch.objects.create(
                        title="Review financial approval",
                        description=f"Approval {approval.pk}. Review the exact revision in the Accounts approval queue.",
                        assignment_type="DEPARTMENT",
                        source_department=policy.department,
                        source_department_name=policy.department.name,
                        source_department_code=policy.department.code,
                        created_by=None,
                        created_by_name="System",
                    )
                    Task.objects.bulk_create(
                        [
                            _build_task(batch=batch, profile=profile)
                            for profile in profiles
                        ]
                    )
                    create_batch_activity(
                        batch=batch,
                        activity_type="BATCH_CREATED",
                        actor=None,
                        metadata={
                            "source": "ledger",
                            "approval": str(approval.pk),
                            "routing_policy": str(policy.pk),
                            "authorized_by": str(policy.created_by_id),
                        },
                    )
                    approval.task_batch = batch
                    approval.save(update_fields=["task_batch"])
            elif approval.task_batch_id:
                from tasks.services.activities import create_task_activity

                for task in approval.task_batch.tasks.select_for_update().filter(
                    status__in=["TO_DO", "IN_PROGRESS", "BLOCKED"]
                ):
                    previous = task.status
                    task.status = (
                        "COMPLETED" if approval.status == "APPROVED" else "CANCELLED"
                    )
                    if task.status == "COMPLETED":
                        task.completed_at = timezone.now()
                    else:
                        task.cancelled_at = timezone.now()
                        task.cancellation_reason = (
                            "Financial approval " + approval.status.lower()
                        )
                    task.save(
                        update_fields=[
                            "status",
                            "updated_at",
                            "completed_at",
                            "cancelled_at",
                            "cancellation_reason",
                        ]
                    )
                    create_task_activity(
                        task=task,
                        activity_type="STATUS_CHANGED",
                        actor=None,
                        previous_value=previous,
                        new_value=task.status,
                        metadata={"source": "ledger", "approval": str(approval.pk)},
                    )
            intent.processed_at = timezone.now()
            intent.failure = ""
            intent.attempts += 1
            intent.save(update_fields=["processed_at", "failure", "attempts"])
    except Exception:
        # Record a safe retry code; provider details and private content stay out.
        from django.db.models import F

        m.IntegrationIntent.objects.filter(pk=pk, processed_at=None).update(
            failure="workflow_retry_required", attempts=F("attempts") + 1
        )
