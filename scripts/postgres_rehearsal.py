"""Pure image/version guards for a separate-database minor-version rehearsal."""

import re

IMAGE = re.compile(r"^postgres:([1-9][0-9]*)\.([0-9]+)-bookworm@sha256:[a-f0-9]{64}$")


def image_version(image):
    match = IMAGE.fullmatch(image)
    if match is None:
        raise ValueError("Use an official PostgreSQL major.minor-bookworm image pinned by SHA256")
    return tuple(map(int, match.groups()))


def restore_images(client, target=None):
    target = client if target is None else target
    if image_version(client)[0] != image_version(target)[0]:
        raise ValueError("This rehearsal supports same-major targets only")
    return client, target


def verify_versions(source_version, target_version_num, client, target):
    restore_images(client, target)
    source = re.match(r"^([0-9]+)\.([0-9]+)(?:\s|$)", source_version)
    if source is None:
        raise ValueError("Unexpected source server version")
    source_major, source_minor = map(int, source.groups())
    target_major, target_minor = image_version(target)
    if source_major != image_version(client)[0]:
        raise ValueError("Actual source major differs from the selected client and target")
    if int(target_version_num) != target_major * 10000 + target_minor:
        raise ValueError("Actual target version differs from the pinned candidate")
    return {
        "same_major": True,
        "target_version": f"{target_major}.{target_minor}",
        "target_server_version_num": int(target_version_num),
        "target_older_minor": target_minor < source_minor,
        "limitation": "Local upstream PostgreSQL compatibility only; RDS behavior and patch equivalence remain unverified.",
    }
