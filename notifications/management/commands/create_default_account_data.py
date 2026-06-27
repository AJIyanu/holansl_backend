ROLE_PERMISSION_RULES = {
    "CEO": {
        "apps": [
            "accounts",
            "crm",
            "procurement",
            "ledger",
            "notifications",
        ],
        "actions": [
            "add",
            "change",
            "view",
            "delete",
        ],
        "extra_codenames": [
            "dispatch_notification",
            "view_all_notification",
            "manage_notificationtemplate",
            "retry_notificationdelivery",
        ],
    },
    "CTO": {
        "apps": [
            "accounts",
            "crm",
            "procurement",
            "ledger",
            "notifications",
        ],
        "actions": [
            "add",
            "change",
            "view",
            "delete",
        ],
        "extra_codenames": [
            "dispatch_notification",
            "view_all_notification",
            "manage_notificationtemplate",
            "retry_notificationdelivery",
        ],
    },
    "Super Admin": {
        "apps": [
            "accounts",
            "crm",
            "procurement",
            "ledger",
            "notifications",
        ],
        "actions": [
            "add",
            "change",
            "view",
            "delete",
        ],
        "extra_codenames": [
            "dispatch_notification",
            "view_all_notification",
            "manage_notificationtemplate",
            "retry_notificationdelivery",
        ],
    },
    # Keep the remaining role definitions unchanged.
}
